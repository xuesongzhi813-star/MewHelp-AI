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
import uuid
from collections.abc import AsyncIterator
from contextlib import aclosing

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.config import get_settings
from app.executor import execute_tool, execute_tool_result
from app.history import SessionStore, trim_history
from app.prompts import build_system_prompt
from app.tools import get_tools
from app.kb.query import build_query_plan
from app.kb.low_confidence import record_low_confidence
from app.kb.answering import (REFUSAL_TEXT, generate_answer, has_forbidden_promise,
                              make_evidence, retrieval_rejection_reason)
from app.kb.contracts import AnswerDecision, QueryPlan, RetrievalResult

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



async def stream_chat(session_id,message,*,store,model,planner=None,answer_model=None,
                      workflow_runtime=None,user_id=None,conversation_id=None,tool_service=None):
    """Keep one catalog for legacy rounds; workflow runtime owns its own round scope."""
    from app.tooling.scope import tool_scope
    import uuid
    snapshot=await tool_service.refresh() if tool_service is not None and workflow_runtime is None else None
    state={'session_id':session_id,'request_id':uuid.uuid4().hex}
    with tool_scope(tool_service,snapshot,state):
        async with aclosing(_stream_chat(session_id,message,store=store,model=model,planner=planner,
            answer_model=answer_model,workflow_runtime=workflow_runtime,user_id=user_id,conversation_id=conversation_id)) as stream:
            async for event in stream:
                yield event


async def _stream_chat(
    session_id: str,
    message: str,
    *,
    store: SessionStore,
    model,
    planner=None,
    answer_model=None,
    workflow_runtime=None,
    user_id=None,
    conversation_id=None,
) -> AsyncIterator[str]:
    """编排一次完整对话轮:可能含一轮工具调用,产出 SSE 帧字符串流。

    - store:满足 SessionStore Protocol 的会话存储(MySQLSessionStore / 测试 Fake);
    - model:聊天基模型(ChatOpenAI),编排内部自行 bind_tools;
    - settings 经模块级 get_settings() 引用获取(可被 monkeypatch)。
    """
    try:
        settings = get_settings()

        if workflow_runtime is not None:
            async with aclosing(workflow_runtime.stream_turn(session_id, message, model=model,user_id=user_id,
                                                            conversation_id=conversation_id)) as stream:
                async for event in stream:
                    yield _sse(event)
            return
        if getattr(settings, 'chat_workflow_enabled', False):
            yield _sse({'type': 'error', 'message': '工作流尚未初始化，请重启服务。'})
            return

        if getattr(settings, "kb_corpus_version", "ch03") == "ch04":
            async with aclosing(_stream_grounded(session_id, message, store=store, model=model,
                                                planner=planner, answer_model=answer_model)) as stream:
                async for frame in stream:
                    yield frame
            return

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

        # 系统注入参数:InjectedToolArg 只把 conversation_id 从模型可见 schema
        # 里摘掉,值必须由调用方给——create_ticket 靠它把工单挂到真实会话上。
        # context 会合并进所有工具的入参,未声明该参数的工具由 pydantic 忽略。
        # 在 start 帧之前取:若 store 取 id 失败,不会留下永远等不到 end 的孤儿
        # start 帧(前端对 error 帧有兜底,但不该依赖它)。
        context = {"conversation_id": store.get_or_create_conversation(session_id)}
        yield _sse({"type": "tool", "name": tool_name, "phase": "start"})
        # 执行器是同步函数,丢线程池;契约保证永不抛异常、永远返回 str
        result = await asyncio.to_thread(
            execute_tool, tool_name, tool_args, context=context,tool_call_id=tool_call['id']
        )
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


async def _gather_round(model, messages, *, final: bool = False):
    kwargs = {"parallel_tool_calls": False}
    if final:
        kwargs["tool_choice"] = "none"
    chunks = []
    gathered = None
    async with asyncio.timeout(get_settings().answer_timeout):
        async for chunk in model.bind_tools(get_tools(), **kwargs).astream(messages):
            if chunk.content:
                chunks.append(str(chunk.content))
            gathered = chunk if gathered is None else gathered + chunk
    return gathered, chunks


async def _stream_grounded(session_id: str, message: str, *, store, model,
                           planner=None, answer_model=None) -> AsyncIterator[str]:
    """ch04发布关口；任何模型草稿在结构化判定前都不进入SSE或最终消息。"""
    try:
        settings = get_settings()
        history = store.get_history(session_id)
        store.append_message(session_id, HumanMessage(content=message))
        plan = await asyncio.to_thread(planner or build_query_plan, message)
        if not isinstance(plan, QueryPlan) or plan.raw_query != message:
            raise ValueError("QueryPlan与本轮原话不符")
        trimmed = _drop_leading_orphan_tool_messages(
            trim_history(history, settings.history_token_budget), session_id)
        messages = [SystemMessage(content=build_system_prompt(settings)), *trimmed,
                    HumanMessage(content=message)]
        route, route_chunks = await _gather_round(model, messages)
        calls = list(route.tool_calls) if route is not None else []
        if len(calls) > 1:
            logger.warning("本轮模型产出%d个工具，仅执行首个", len(calls))
        call = calls[0] if calls else None
        if (plan.intent == "knowledge"
                or (call is None and plan.requires_knowledge)):
            # 单轮计划已确定是知识问题：在执行前选FAQ，避免模型误选商品等
            # 工具后才因没有知识证据拒答；业务意图仍按原工具执行一次。
            call = {"name": "query_faq", "args": {"keyword": message},
                    "id": "forced_" + uuid.uuid4().hex, "type": "tool_call"}
        decision = None
        evidence_result = None
        pool_source = None
        conversation_id = None
        final_chunks = route_chunks
        if call is not None:
            conversation_id = store.get_or_create_conversation(session_id)
            context = {"conversation_id": conversation_id, "query_plan": plan}
            name = call["name"]
            yield _sse({"type": "tool", "name": name, "phase": "start"})
            execution = await asyncio.to_thread(execute_tool_result, name, call["args"],
                context=context, tool_call_id=call["id"])
            ai_tool = AIMessage(content="", tool_calls=[call])
            tool_message = ToolMessage(content=execution.content, tool_call_id=call["id"], status=execution.status)
            store.append_message(session_id, ai_tool)
            store.append_message(session_id, tool_message)
            yield _sse({"type": "tool", "name": name, "phase": "end"})
            if execution.status != "success":
                raise RuntimeError("工具服务失败")
            if name == "query_faq":
                if not isinstance(execution.artifact, RetrievalResult):
                    raise RuntimeError("知识工具缺少结构化证据")
                evidence_result = execution.artifact
                if evidence_result.plan != plan:
                    raise RuntimeError("知识证据不属于本轮计划")
                rejection = retrieval_rejection_reason(evidence_result)
                if rejection:
                    decision = AnswerDecision(can_answer=False, reason=rejection, answer="")
                    pool_source = "retrieval_low_conf"
                else:
                    if answer_model is None:
                        from app.llm import build_answer_model
                        answer_model = build_answer_model(settings)
                    decision = await generate_answer(plan, evidence_result, model=answer_model)
                    pool_source = "self_check" if not decision.can_answer else None
                final_chunks = [decision.answer] if decision.can_answer else [REFUSAL_TEXT]
            elif plan.intent == "knowledge" or plan.requires_knowledge:
                # 已执行一个业务工具，不再补第二个；没有知识证据不能发政策猜测。
                pool_source = "self_check"
                decision = AnswerDecision(can_answer=False, reason="本轮知识问题未取得知识证据", answer="")
                final_chunks = [REFUSAL_TEXT]
            else:
                _, final_chunks = await _gather_round(model, [*messages, ai_tool, tool_message], final=True)
        final_text = "".join(final_chunks)
        if has_forbidden_promise(final_text):
            pool_source = "self_check"
            decision = AnswerDecision(can_answer=False, reason="回答包含禁止承诺", answer="")
            final_chunks, final_text = [REFUSAL_TEXT], REFUSAL_TEXT
        if pool_source:
            if conversation_id is None:
                conversation_id = store.get_or_create_conversation(session_id)
            await asyncio.to_thread(record_low_confidence, message, conversation_id,
                                    pool_source, decision.reason)
        # 持久化只接受验证后的文本；池写入或持久化失败时不能先展示成功正文。
        store.append_message(session_id, AIMessage(content=final_text))
        if decision is not None and decision.can_answer and evidence_result is not None:
            used = set(decision.used_citation_numbers)
            items = [{"number": e.number, "chunk_id": e.hit.chunk_id,
                      "section_path": e.hit.section_path, "text": e.hit.answer,
                      "original_url": f"/kb?chunk_id={e.hit.chunk_id}"}
                     for e in make_evidence(evidence_result.hits) if e.number in used]
            yield _sse({"type": "citations", "items": items})
        for content in final_chunks:
            # 质量关口已通过；分片仍遵守原token SSE协议。
            for start in range(0, len(content), 32):
                yield _sse({"type": "token", "content": content[start:start + 32]})
                await asyncio.sleep(0)
        yield _sse({"type": "done"})
    except Exception as exc:
        logger.warning("ch04聊天编排失败:%s", type(exc).__name__)
        yield _sse({"type": "error", "message": "处理消息时出现错误，请稍后重试或联系人工客服。"})
