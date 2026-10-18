"""chat_service 编排测试:FakeChatModel + FakeStore 全内存,零真实库零真实 LLM。

复用 test_chat_api.py 的 Fake 手法(替身记录收到的 messages / 调用参数),
外加:
- Fake 模型的 bind_tools 返回自身并记录调用(断言调用①绑工具、调用②不绑);
- Fake 模型每轮 astream 消耗一份脚本化的 chunk 列表,tool_call 用分片
  chunk(name 片 + args 两半)产出,顺带验证编排层的累加拼装;
- FakeStore 记录 append 调用序列,验证落库时序。

execute_tool 走真实执行器:query_logistics 是纯 Mock 工具不碰库;
「未注册工具名」场景直接用执行器契约(返回错误文本,不抛)覆盖。
"""

import json

import pytest
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.chat_service import stream_chat
from app.tools import get_tools


class FakeChatModel:
    """测试替身:bind_tools 返回自身并记录调用;astream 按轮次消耗脚本化 chunks。

    rounds[i] 是第 i 次 astream 调用要 yield 的 chunk 列表;
    error_on_round 指定某一轮直接抛异常(None 表示永不抛)。
    """

    def __init__(
        self,
        rounds: list[list[AIMessageChunk]],
        error_on_round: int | None = None,
        error: Exception | None = None,
    ) -> None:
        self._rounds = rounds
        self._error_on_round = error_on_round
        self._error = error
        self.received: list[list[BaseMessage]] = []
        self.bind_tools_calls: list[dict] = []
        self._round = 0

    def bind_tools(self, tools, **kwargs):
        self.bind_tools_calls.append({"tools": list(tools), "kwargs": kwargs})
        return self

    async def astream(self, messages, **kwargs):
        round_idx = self._round
        self._round += 1
        self.received.append(list(messages))
        if self._error_on_round is not None and round_idx == self._error_on_round:
            raise self._error if self._error is not None else RuntimeError("模型炸了")
        for chunk in self._rounds[round_idx]:
            yield chunk


class FakeStore:
    """测试替身:全内存会话存储,记录 append 调用序列(含 session_id)。"""

    def __init__(self, history: list[BaseMessage] | None = None) -> None:
        self.history = list(history or [])
        self.appended: list[tuple[str, BaseMessage]] = []

    def get_history(self, session_id: str) -> list[BaseMessage]:
        return list(self.history)

    def append_message(self, session_id: str, message: BaseMessage) -> None:
        self.appended.append((session_id, message))


def parse_sse(frames: list[str]) -> list[dict]:
    """解析 stream_chat 产出的 SSE 帧字符串列表(与 test_chat_api 同款校验)。"""
    events: list[dict] = []
    for frame in frames:
        assert frame.startswith("data: ") and frame.endswith("\n\n"), f"非法 SSE 帧:{frame!r}"
        events.append(json.loads(frame[len("data: ") :]))
    return events


async def collect(agen) -> list[str]:
    return [frame async for frame in agen]


def tool_call_chunks(
    name: str, args_json: str, call_id: str
) -> list[AIMessageChunk]:
    """把一次 tool_call 拆成分片 chunks:名字片 + args 前半片 + args 后半片。

    name/args 分片喂给编排层,验证 gathered + chunk 的官方拼装姿势。
    """
    half = len(args_json) // 2
    return [
        AIMessageChunk(
            content="",
            tool_call_chunks=[{"name": name, "args": args_json[:half], "id": call_id, "index": 0}],
        ),
        AIMessageChunk(
            content="",
            tool_call_chunks=[{"name": None, "args": args_json[half:], "id": None, "index": 0}],
        ),
    ]


async def test_tool_round_full_frame_sequence():
    """场景A(工具轮):token → tool start → tool end → token... → done。"""
    fake = FakeChatModel(
        rounds=[
            # 调用①:前置文本 + 分片 tool_call(query_logistics)
            [
                AIMessageChunk(content="我帮您查一下"),
                *tool_call_chunks("query_logistics", '{"order_id": "1001"}', "call_1"),
            ],
            # 调用②:收敛作答的文本 chunks
            [AIMessageChunk(content="您的包裹正在派送中"), AIMessageChunk(content=",预计明天送达")],
        ]
    )
    store = FakeStore()

    frames = await collect(
        stream_chat("s-tool", "订单 1001 到哪了", store=store, model=fake)
    )
    events = parse_sse(frames)

    # 帧序列:前置 token → tool start/end → 两个 token → done
    assert [e["type"] for e in events] == [
        "token", "tool", "tool", "token", "token", "done",
    ]
    assert events[0] == {"type": "token", "content": "我帮您查一下"}
    assert events[1] == {"type": "tool", "name": "query_logistics", "phase": "start"}
    assert events[2] == {"type": "tool", "name": "query_logistics", "phase": "end"}
    assert events[3]["content"] == "您的包裹正在派送中"
    assert events[4]["content"] == ",预计明天送达"
    assert events[-1] == {"type": "done"}


async def test_tool_round_second_call_messages_and_bind_params():
    """场景A续:调用②收到的消息列表正确;两次 bind 的参数符合 Ruling 3。

    ch02 调整(Ruling 3):调用②从「完全不绑工具」改为 bind_tools +
    tool_choice="none"(防 DSML 泄漏,见 chat_service 第 6 步注释)——
    断言口径由「bind 仅 1 次」改为「bind 2 次且参数各异」。
    """
    fake = FakeChatModel(
        rounds=[
            [*tool_call_chunks("query_logistics", '{"order_id": "1001"}', "call_1")],
            [AIMessageChunk(content="查到了")],
        ]
    )
    store = FakeStore()

    await collect(stream_chat("s-msgs", "物流", store=store, model=fake))

    # 调用①绑工具且 parallel_tool_calls=False、不指定 tool_choice;
    # 调用②再 bind 一次:tool_choice="none"(服务器侧拒绝工具调用)
    assert len(fake.bind_tools_calls) == 2
    first_bind, second_bind = fake.bind_tools_calls
    assert first_bind["tools"] == get_tools()
    assert first_bind["kwargs"].get("parallel_tool_calls") is False
    assert "tool_choice" not in first_bind["kwargs"]
    assert second_bind["tools"] == get_tools()
    assert second_bind["kwargs"].get("tool_choice") == "none"
    assert second_bind["kwargs"].get("parallel_tool_calls") is False
    assert len(fake.received) == 2

    # 调用②的消息:System + Human + AIMessage(tool_calls) + ToolMessage
    second = fake.received[1]
    assert len(second) == 4
    assert isinstance(second[0], SystemMessage)
    assert isinstance(second[1], HumanMessage) and second[1].content == "物流"
    ai_msg = second[2]
    assert isinstance(ai_msg, AIMessage)
    assert len(ai_msg.tool_calls) == 1
    assert ai_msg.tool_calls[0]["name"] == "query_logistics"
    assert ai_msg.tool_calls[0]["args"] == {"order_id": "1001"}
    tool_msg = second[3]
    assert isinstance(tool_msg, ToolMessage)
    assert tool_msg.tool_call_id == "call_1"
    # 真实执行器跑了 Mock 的 query_logistics,结果是非空 JSON 文本
    assert tool_msg.content and "order_id" in tool_msg.content

    # 调用①的消息不含回灌的 AIMessage/ToolMessage
    first = fake.received[0]
    assert len(first) == 2
    assert isinstance(first[0], SystemMessage)
    assert isinstance(first[1], HumanMessage)


async def test_plain_text_round_matches_ch01():
    """场景B(纯文本轮):无 tool_calls → token 帧 + done,与 ch01 行为一致。"""
    fake = FakeChatModel(rounds=[[AIMessageChunk(content="你好"), AIMessageChunk(content="呀")]])
    store = FakeStore()

    frames = await collect(stream_chat("s-plain", "你好", store=store, model=fake))
    events = parse_sse(frames)

    assert [e["type"] for e in events] == ["token", "token", "done"]
    assert "".join(e["content"] for e in events if e["type"] == "token") == "你好呀"
    # 无工具帧、只发生一次模型调用
    assert all(e["type"] != "tool" for e in events)
    assert len(fake.received) == 1
    assert len(fake.bind_tools_calls) == 1  # 调用①仍绑了工具,只是模型没用


async def test_append_message_order_and_types():
    """场景C(落库时序):[Human, AIMessage(tool_calls), ToolMessage, AIMessage(最终)]。"""
    fake = FakeChatModel(
        rounds=[
            [
                AIMessageChunk(content="我帮您查一下"),
                *tool_call_chunks("query_logistics", '{"order_id": "1001"}', "call_1"),
            ],
            [AIMessageChunk(content="已查到")],
        ]
    )
    store = FakeStore()

    await collect(stream_chat("s-order", "查物流", store=store, model=fake))

    appended = store.appended
    assert len(appended) == 4
    assert all(sid == "s-order" for sid, _ in appended)

    m0 = appended[0][1]
    assert isinstance(m0, HumanMessage) and m0.content == "查物流"

    m1 = appended[1][1]
    assert isinstance(m1, AIMessage)
    assert m1.content == "我帮您查一下"
    assert len(m1.tool_calls) == 1
    assert m1.tool_calls[0]["name"] == "query_logistics"
    assert m1.tool_calls[0]["id"] == "call_1"

    m2 = appended[2][1]
    assert isinstance(m2, ToolMessage)
    assert m2.tool_call_id == "call_1"
    assert m2.content

    m3 = appended[3][1]
    assert isinstance(m3, AIMessage)
    assert m3.content == "已查到"
    assert m3.tool_calls == []


async def test_unknown_tool_name_still_completes():
    """场景D(工具执行失败):未注册工具名 → 执行器契约兜底,流程仍走完并 done。"""
    fake = FakeChatModel(
        rounds=[
            [*tool_call_chunks("ghost_tool", "{}", "call_ghost")],
            [AIMessageChunk(content="抱歉,这个功能暂时出了点问题")],
        ]
    )
    store = FakeStore()

    frames = await collect(stream_chat("s-ghost", "随便查个啥", store=store, model=fake))
    events = parse_sse(frames)

    assert [e["type"] for e in events] == ["tool", "tool", "token", "done"]
    assert events[0] == {"type": "tool", "name": "ghost_tool", "phase": "start"}
    assert events[1] == {"type": "tool", "name": "ghost_tool", "phase": "end"}
    assert events[-1] == {"type": "done"}

    # 回灌给模型的是执行器的中文错误文本(含「不存在」),不是异常
    tool_msg = store.appended[2][1]
    assert isinstance(tool_msg, ToolMessage)
    assert "不存在" in tool_msg.content


async def test_second_call_error_yields_error_without_done():
    """场景E(异常路径):调用②抛 → error 帧、无 done、最终回复不落库。"""
    fake = FakeChatModel(
        rounds=[
            [*tool_call_chunks("query_order", '{"order_id": "1001"}', "call_err")],
            [AIMessageChunk(content="不会走到这里")],
        ],
        error_on_round=1,
        error=RuntimeError("模型炸了"),
    )
    store = FakeStore()

    frames = await collect(stream_chat("s-err2", "查订单", store=store, model=fake))
    events = parse_sse(frames)

    assert events[-1]["type"] == "error"
    assert "模型炸了" in events[-1]["message"]
    assert all(e["type"] != "done" for e in events)
    # 异常发生在工具执行后:Human / AIMessage(tool_calls) / ToolMessage 已落库,
    # 最终 assistant 回复未落库(ch01 孤儿消息现状,设计已记录)
    assert [type(m).__name__ for _, m in store.appended] == [
        "HumanMessage", "AIMessage", "ToolMessage",
    ]


async def test_first_call_error_yields_error_without_done():
    """场景E补充:调用①抛 → error 帧、无 done、无工具帧。"""
    fake = FakeChatModel(rounds=[[]], error_on_round=0, error=RuntimeError("连不上"))
    store = FakeStore()

    frames = await collect(stream_chat("s-err1", "你好", store=store, model=fake))
    events = parse_sse(frames)

    assert [e["type"] for e in events] == ["error"]
    assert "连不上" in events[0]["message"]
    # 只有 user 消息落库(孤儿消息现状与 ch01 一致)
    assert [type(m).__name__ for _, m in store.appended] == ["HumanMessage"]


async def test_second_call_ignores_tool_calls_defensively():
    """Ruling 3 防御层:极端情况下 provider 无视 tool_choice="none",
    调用②仍在 chunk 里吐 tool_calls——编排层不累加、不执行,只取 content
    作答:无第二对 tool 帧、无第二次工具执行、done 收尾、落库只含文本。
    """
    fake = FakeChatModel(
        rounds=[
            [*tool_call_chunks("query_logistics", '{"order_id": "1001"}', "call_1")],
            [
                AIMessageChunk(content="根据查询结果"),
                # 调用②违规产出的第二个 tool_call(真实 provider 不该发生)
                *tool_call_chunks("query_order", '{"order_id": "1001"}', "call_bad"),
                AIMessageChunk(content=",您的包裹在派送中"),
            ],
        ]
    )
    store = FakeStore()

    frames = await collect(stream_chat("s-defend", "查物流", store=store, model=fake))
    events = parse_sse(frames)

    # 全程只有调用①那一对 tool 帧;违规 tool_call 没有触发第二对
    tool_events = [e for e in events if e["type"] == "tool"]
    assert tool_events == [
        {"type": "tool", "name": "query_logistics", "phase": "start"},
        {"type": "tool", "name": "query_logistics", "phase": "end"},
    ]
    assert events[-1] == {"type": "done"}

    # 只发生了两次模型调用,没有第三次(不存在 Agent Loop)
    assert len(fake.received) == 2
    # 落库的最终回复是纯文本,不带 tool_calls
    final = store.appended[-1][1]
    assert isinstance(final, AIMessage)
    assert final.content == "根据查询结果,您的包裹在派送中"
    assert final.tool_calls == []


async def test_multiple_tool_calls_only_first_executed(caplog):
    """单轮双保险:模型仍产出多个 tool_calls 时,只执行第一个并记警告。"""
    fake = FakeChatModel(
        rounds=[
            [
                AIMessageChunk(content="", tool_call_chunks=[
                    {"name": "query_order", "args": '{"order_', "id": "call_a", "index": 0},
                ]),
                AIMessageChunk(content="", tool_call_chunks=[
                    {"name": None, "args": 'id": "1001"}', "id": None, "index": 0},
                ]),
                AIMessageChunk(content="", tool_call_chunks=[
                    {"name": "query_faq", "args": '{"keyword": "退货"}', "id": "call_b", "index": 1},
                ]),
            ],
            [AIMessageChunk(content="好了")],
        ]
    )
    store = FakeStore()

    frames = await collect(stream_chat("s-multi", "订单和退货政策", store=store, model=fake))
    events = parse_sse(frames)

    # 只执行第一个 tool_call:tool 帧的 name 是 query_order,且只有一对 start/end
    tool_events = [e for e in events if e["type"] == "tool"]
    assert tool_events == [
        {"type": "tool", "name": "query_order", "phase": "start"},
        {"type": "tool", "name": "query_order", "phase": "end"},
    ]
    assert events[-1] == {"type": "done"}

    # 落库的 AIMessage 也只带第一个 tool_call
    ai_msg = store.appended[1][1]
    assert [tc["name"] for tc in ai_msg.tool_calls] == ["query_order"]

    # 记录了警告日志(caplog 默认捕获已发生的 WARNING 记录)
    assert any("tool_calls" in rec.getMessage() for rec in caplog.records)


async def test_history_trimmed_and_included():
    """历史参与拼装:store 里的旧消息进 trim_history,本轮 Human 在末尾。"""
    fake = FakeChatModel(rounds=[[AIMessageChunk(content="嗯")]])
    store = FakeStore(
        history=[
            HumanMessage(content="早上好"),
            AIMessage(content="早上好!有什么可以帮您?"),
        ]
    )

    await collect(stream_chat("s-hist", "在吗", store=store, model=fake))

    msgs = fake.received[0]
    assert [type(m).__name__ for m in msgs] == [
        "SystemMessage", "HumanMessage", "AIMessage", "HumanMessage",
    ]
    assert msgs[1].content == "早上好"
    assert msgs[3].content == "在吗"


@pytest.mark.parametrize(
    ("frames_count", "expect"),
    [(0, "空流也安全"), (1, "单帧")],
)
async def test_empty_stream_safe(frames_count, expect):
    """模型一个 chunk 都不吐(或只吐一个):不炸、无 tool_calls、done 收尾。"""
    chunks = [AIMessageChunk(content="x")] * frames_count
    fake = FakeChatModel(rounds=[chunks])
    store = FakeStore()

    frames = await collect(stream_chat("s-empty", "hi", store=store, model=fake))
    events = parse_sse(frames)
    assert events[-1] == {"type": "done"}
    assert store.appended[-1][1].content == ("x" if frames_count else "")
