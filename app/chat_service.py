"""ch02 聊天编排:模型定工具 → 执行 → 回灌收敛,全程 SSE 流式帧。

单轮工具调用约束(设计 §7),三层保证:
1. bind_tools(..., parallel_tool_calls=False) 从请求参数上抑制模型并行多调;
2. 回灌后的调用②带 tool_choice="none"(Ruling 3,详见第 6 步注释)——工具 schema
   仍可见,但服务器侧直接拒绝工具调用,模型只能收敛作答(硬保证,不做 Agent Loop);
3. 即便模型仍在一轮里产出多个 tool_calls,编排层也只执行第一个并记日志警告(双保险)。

帧协议(与 ch01 main.py 逐字节同款序列化:json.dumps(ensure_ascii=False) + "data: " 前缀 + "\n\n"):
- {"type":"token","content":...}  文本增量
- {"type":"tool","name":...,"phase":"start"|"end"}  工具轨迹(新增)
- {"type":"done"} / {"type":"error","message":...}

异常契约:任何阶段抛异常 → yield error 帧后直接 return(不 yield done);
此时孤儿消息问题(如 user 消息已落库但无 assistant 回复)维持 ch01 现状,设计已记录。
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.config import get_settings
from app.executor import execute_tool
from app.history import SessionStore, trim_history
from app.prompts import build_system_prompt
from app.tools import get_tools

logger = logging.getLogger(__name__)


def _sse(payload: dict) -> str:
    """与 ch01 main.py 完全一致的 SSE 帧序列化。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _error_text(exc: Exception) -> str:
    """异常帧的中文文案:附原始信息便于排查,但不泄堆栈给前端。"""
    return f"处理消息时出现错误,请稍后重试或联系人工客服({exc})"


def _drop_leading_orphan_tool_messages(
    messages: list[BaseMessage], session_id: str
) -> list[BaseMessage]:
    """trim 后清理开头连续的孤儿 ToolMessage(整分支评审 I-1)。

    动机:trim_history 委托的 trim_messages(strategy="last") 从头丢旧消息,
    token 预算边界可能恰好落在 AIMessage(tool_calls) 与其 ToolMessage 配对
    中间——裁剪结果以孤儿 ToolMessage 开头。孤儿 ToolMessage(前面没有
    与之配对的 AIMessage(tool_calls),即 tool_call_id 无对应调用)发
    OpenAI 协议 API 即 400,与 session_store 已防御的「尾部孤儿」同毒、
    但发生在 trim 侧;本清理与那边互补。

    清理口径:只处理**开头连续的孤儿 ToolMessage**——trim 只能从头部截断,
    这是唯一现实形态;序列中间的配对断裂属于库级数据损坏,超出本层职责,
    保持现状(配对判定:开头之前不存在任何 AIMessage(tool_calls),自然
    无 tool_call_id 可配)。

    清理发生时记一行 WARNING(会话 id + 被丢的 tool_call_id 列表),便于排查。
    """
    orphans: list[ToolMessage] = []
    kept = list(messages)
    # 开头连续的 ToolMessage 都没有前置的 AIMessage(tool_calls) 可配 → 全是孤儿
    while kept and isinstance(kept[0], ToolMessage):
        orphans.append(kept.pop(0))
    if orphans:
        logger.warning(
            "trim 边界拆散了工具调用配对,丢弃开头 %d 条孤儿 ToolMessage"
            "(session_id=%s, tool_call_id=%s)",
            len(orphans),
            session_id,
            [m.tool_call_id for m in orphans],
        )
    return kept


async def stream_chat(
    session_id: str,
    message: str,
    *,
    store: SessionStore,
    model,
) -> AsyncIterator[str]:
    """编排一次完整对话轮:可能含一轮工具调用,产出 SSE 帧字符串流。

    - store:满足 SessionStore Protocol 的会话存储(MySQLSessionStore / 测试 Fake);
    - model:聊天基模型(ChatOpenAI),编排内部自行 bind_tools;
    - settings 经模块级 get_settings() 引用获取(可被 monkeypatch)。
    """
    try:
        settings = get_settings()

        # 1) 载历史(不含本轮 user 消息)+ 本轮 user 消息先落库
        history = store.get_history(session_id)
        store.append_message(session_id, HumanMessage(content=message))

        # 2) 拼 [System] + trim_history(历史) + [Human]
        #    trim 后先清掉开头可能的孤儿 ToolMessage(评审 I-1:预算边界拆散
        #    AIMessage(tool_calls)/ToolMessage 配对时,孤儿发 API 即 400)
        trimmed = _drop_leading_orphan_tool_messages(
            trim_history(history, settings.history_token_budget),
            session_id,
        )
        msgs: list[BaseMessage] = [
            SystemMessage(content=build_system_prompt(settings)),
            *trimmed,
            HumanMessage(content=message),
        ]

        # 3) 调用①:绑工具流式;content 出 token 帧,chunk 累加拼 tool_calls
        gathered = None
        async for chunk in model.bind_tools(
            get_tools(), parallel_tool_calls=False
        ).astream(msgs):
            content = chunk.content
            if content:
                yield _sse({"type": "token", "content": content})
            # 官方姿势:chunk 逐个相加即可拼出完整 tool_calls
            gathered = chunk if gathered is None else gathered + chunk

        tool_calls = list(gathered.tool_calls) if gathered is not None else []

        # 4) 无工具调用:与 ch01 行为一致,直接落最终回复并 done
        if not tool_calls:
            final_content = str(gathered.content) if gathered is not None else ""
            store.append_message(session_id, AIMessage(content=final_content or ""))
            yield _sse({"type": "done"})
            return

        # 5) 有工具调用:单轮约束下只执行第一个
        if len(tool_calls) > 1:
            # parallel_tool_calls=False 已抑制并行多调,这里再兜一层(双保险)
            logger.warning(
                "模型一轮产出 %d 个 tool_calls(parallel_tool_calls=False 未生效?),只执行第一个:%s",
                len(tool_calls),
                [tc.get("name") for tc in tool_calls],
            )
        tool_call = tool_calls[0]
        tool_name = tool_call["name"]
        tool_args = tool_call["args"]
        ai_tool_msg = AIMessage(content=gathered.content or "", tool_calls=[tool_call])

        yield _sse({"type": "tool", "name": tool_name, "phase": "start"})
        # 执行器是同步函数,丢线程池;契约保证永不抛异常、永远返回 str
        result = await asyncio.to_thread(execute_tool, tool_name, tool_args)
        tool_msg = ToolMessage(content=result, tool_call_id=tool_call["id"])
        # 两条 append 必须都在工具执行后、回灌调用前落库
        store.append_message(session_id, ai_tool_msg)
        store.append_message(session_id, tool_msg)
        yield _sse({"type": "tool", "name": tool_name, "phase": "end"})

        # 6) 调用②:回灌工具结果。Ruling 3:单轮硬保证从「不绑工具(无 schema
        # 可调)」升级为「工具 schema 可见,但 tool_choice="none" 由服务器侧
        # 直接拒绝工具调用」。动机:Task 8 样例评估实测,完全不绑工具时
        # thinking-disabled 的 DeepSeek 若还想查第二项信息,会把 DSML 工具
        # 调用标记(<｜｜DSML｜｜ calls>...)当正文吐进 token 流(Prompt 迭代
        # 2 轮压不住,模型内秉回落);实测 tool_choice="none" 可完美抑制
        # (无 tool_calls、无泄漏、自然作答)。注意调用②单独 bind(不与调用①
        # 共享 bound 实例,两次 bind 参数不同)。
        # 防御:即便 provider 无视 none 仍在 chunk 里带 tool_calls,本层也不
        # 累加、不执行任何工具,只取 content 作答——单轮约束在代码层依然成立。
        final_content = ""
        async for chunk in model.bind_tools(
            get_tools(), parallel_tool_calls=False, tool_choice="none"
        ).astream([*msgs, ai_tool_msg, tool_msg]):
            content = chunk.content
            if content:
                final_content += content
                yield _sse({"type": "token", "content": content})

        # 7) 最终 assistant 回复落库 + done
        store.append_message(session_id, AIMessage(content=final_content))
        yield _sse({"type": "done"})
    except Exception as exc:  # noqa: BLE001 - 任何阶段异常都转 error 帧,不打断流
        logger.warning("聊天编排异常(session_id=%s):%s: %s", session_id, type(exc).__name__, exc)
        yield _sse({"type": "error", "message": _error_text(exc)})
        return
