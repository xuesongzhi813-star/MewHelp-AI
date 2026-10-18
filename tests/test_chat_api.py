"""API 层测试:/chat 改走 chat_service 编排后的适配版(ch02 Task 7)。

适配点(store 换 MySQLSessionStore、模型经 stream_chat 消费):
- FakeChatModel 升级为与 test_chat_service 同款的「轮次脚本 + bind_tools」替身:
  stream_chat 会先 bind_tools(...).astream(...),ch01 的裸 astream 替身已不可用;
- app.state.session_store 每个用例换上全新的 InMemorySessionStore(满足
  SessionStore 协议、零真实 MySQL 依赖),结束后还原;
- 断言点保持 ch01 口径:纯文本轮 token…done、多轮历史续接、模型异常转 error 帧。
"""

import json

import pytest
from langchain_core.messages import (
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from sqlalchemy.exc import OperationalError

from app.dependencies import get_chat_model
from app.history import InMemorySessionStore
from app.main import app


class FakeChatModel:
    """测试替身:bind_tools 返回自身并记录调用;astream 按轮次消耗脚本化 chunks。

    与 test_chat_service.py 的 FakeChatModel 同款手法(stream_chat 的消费方式):
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


def tool_call_chunks(
    name: str, args_json: str, call_id: str
) -> list[AIMessageChunk]:
    """把一次 tool_call 拆成分片 chunks:名字片 + args 前半片 + args 后半片。

    与 test_chat_service.py 同款:喂给编排层验证 gathered + chunk 的拼装姿势。
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


class UnreachableStore:
    """模拟 MySQL 不可达:任何 store 操作都抛 OperationalError。

    满足 SessionStore 协议签名,用于验证 stream_chat 兜住 store 层异常。
    """

    def get_history(self, session_id: str) -> list[BaseMessage]:
        raise OperationalError(
            "SELECT ...", {}, Exception("(pymysql.OperationalError) 无法连接 MySQL")
        )

    def append_message(self, session_id: str, message: BaseMessage) -> None:
        raise OperationalError(
            "INSERT ...", {}, Exception("(pymysql.OperationalError) 无法连接 MySQL")
        )


@pytest.fixture(autouse=True)
def memory_store():
    """每个用例换上全新的内存 store 并在结束后还原。

    app 里的正式 store 是 MySQLSessionStore(构造期不连库,但操作会真连),
    API 层测试不碰真实 MySQL:InMemorySessionStore 与其满足同一 SessionStore
    协议,可直接替换 app.state.session_store。
    """
    original = app.state.session_store
    store = InMemorySessionStore(max_sessions=100)
    app.state.session_store = store
    yield store
    app.state.session_store = original


def parse_sse(text: str) -> list[dict]:
    """解析 SSE 帧:每帧形如 `data: {json}\\n\\n`。"""
    events: list[dict] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        assert block.startswith("data: "), f"非法 SSE 帧: {block!r}"
        events.append(json.loads(block[len("data: ") :]))
    return events


async def test_chat_streams_tokens_then_done(client):
    fake = FakeChatModel(rounds=[[AIMessageChunk(content="你好"), AIMessageChunk(content="呀")]])
    app.dependency_overrides[get_chat_model] = lambda: fake

    response = await client.post("/chat", json={"session_id": "s-tokens", "message": "你好"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)

    tokens = [e["content"] for e in events if e["type"] == "token"]
    assert "".join(tokens) == "你好呀"

    done = [e for e in events if e["type"] == "done"]
    assert len(done) == 1
    assert events[-1] == done[0]
    # 纯文本轮无工具帧
    assert all(e["type"] != "tool" for e in events)


async def test_second_turn_sees_first_turn_history(client):
    fake = FakeChatModel(rounds=[[AIMessageChunk(content="好的")]])
    app.dependency_overrides[get_chat_model] = lambda: fake

    await client.post("/chat", json={"session_id": "s-history", "message": "你好"})
    await client.post("/chat", json={"session_id": "s-history", "message": "在吗"})

    messages = fake.received[1]
    # 先断总数:历史多出杂项消息时位置索引断言才不会静默通过(最终评审 N-4)
    assert len(messages) == 4
    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[1], HumanMessage) and messages[1].content == "你好"
    assert messages[2].content == "好的"
    assert isinstance(messages[3], HumanMessage) and messages[3].content == "在吗"


async def test_llm_error_yields_error_event(client):
    fake = FakeChatModel(rounds=[[]], error_on_round=0, error=RuntimeError("模型炸了"))
    app.dependency_overrides[get_chat_model] = lambda: fake

    response = await client.post("/chat", json={"session_id": "s-error", "message": "你好"})

    assert response.status_code == 200
    events = parse_sse(response.text)
    assert events[-1]["type"] == "error"
    assert "模型炸了" in events[-1]["message"]
    assert all(e["type"] != "done" for e in events)


async def test_empty_message_rejected(client):
    response = await client.post("/chat", json={"session_id": "s-empty", "message": ""})
    assert response.status_code == 422


async def test_health_ok(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_chat_tool_round_streams_tool_frames(client):
    """新增:API 层走通工具轮——SSE 响应体含 tool start/end 帧与 token/done。

    fake 模型调用①产出分片 tool_call(query_logistics 是纯 Mock 工具不碰库),
    调用②收敛作答;断言整条帧序走全 HTTP 链路(StreamingResponse)。
    """
    fake = FakeChatModel(
        rounds=[
            # 调用①:前置文本 + 分片 tool_call
            [
                AIMessageChunk(content="我帮您查一下"),
                *tool_call_chunks("query_logistics", '{"order_id": "1001"}', "call_1"),
            ],
            # 调用②:收敛作答
            [AIMessageChunk(content="您的包裹正在派送中")],
        ]
    )
    app.dependency_overrides[get_chat_model] = lambda: fake

    response = await client.post(
        "/chat", json={"session_id": "s-api-tool", "message": "订单 1001 到哪了"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)

    # 帧序:前置 token → tool start/end → 收敛 token → done
    assert [e["type"] for e in events] == ["token", "tool", "tool", "token", "done"]
    assert events[0] == {"type": "token", "content": "我帮您查一下"}
    assert events[1] == {"type": "tool", "name": "query_logistics", "phase": "start"}
    assert events[2] == {"type": "tool", "name": "query_logistics", "phase": "end"}
    assert events[3] == {"type": "token", "content": "您的包裹正在派送中"}
    assert events[-1] == {"type": "done"}


async def test_store_unreachable_yields_error_event(client):
    """新增:MySQL 不可达 → store 操作抛 OperationalError → error 帧、无 done。

    覆盖链路:store 异常发生在 get_history(模型调用之前),stream_chat 的
    try 覆盖 store 操作,HTTP 层仍是 200 + SSE error 帧收尾;
    autouse 的 memory_store fixture 保证用例结束后还原正式 store。
    """
    app.state.session_store = UnreachableStore()
    fake = FakeChatModel(rounds=[[AIMessageChunk(content="不该被消费")]])
    app.dependency_overrides[get_chat_model] = lambda: fake

    response = await client.post("/chat", json={"session_id": "s-db-down", "message": "你好"})

    assert response.status_code == 200
    events = parse_sse(response.text)
    assert [e["type"] for e in events] == ["error"]
    assert "稍后重试" in events[0]["message"]
    # store 在模型调用之前就炸了:模型根本没收到消息
    assert fake.received == []
