"""ch02 聊天编排:模型定工具 → 执行 → 回灌收敛,全程 SSE 流式帧。

单轮工具调用约束(设计 §7),三层保证:
1. bind_tools(..., parallel_tool_calls=False) 从请求参数上抑制模型并行多调;
2. 回灌后的调用②**不绑工具**——模型没有工具可调,只能收敛作答(硬保证,不做 Agent Loop);
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
        msgs: list[BaseMessage] = [
            SystemMessage(content=build_system_prompt(settings)),
            *trim_history(history, settings.history_token_budget),
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

        # 6) 调用②:回灌工具结果,**不绑工具**逼收敛(单轮硬保证)
        final_content = ""
        async for chunk in model.astream([*msgs, ai_tool_msg, tool_msg]):
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
