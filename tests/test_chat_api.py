import json

from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage

from app.dependencies import get_chat_model
from app.main import app


class FakeChatModel:
    """测试替身:逐段 yield AIMessageChunk,并记录每次收到的 messages。"""

    def __init__(self, chunks: list[str], error: Exception | None = None) -> None:
        self._chunks = chunks
        self._error = error
        self.received: list[list] = []

    async def astream(self, messages, **kwargs):
        self.received.append(list(messages))
        if self._error is not None:
            raise self._error
        for chunk in self._chunks:
            yield AIMessageChunk(content=chunk)


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
    fake = FakeChatModel(chunks=["你好", "呀"])
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


async def test_second_turn_sees_first_turn_history(client):
    fake = FakeChatModel(chunks=["好的"])
    app.dependency_overrides[get_chat_model] = lambda: fake

    await client.post("/chat", json={"session_id": "s-history", "message": "你好"})
    await client.post("/chat", json={"session_id": "s-history", "message": "在吗"})

    messages = fake.received[1]
    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[1], HumanMessage) and messages[1].content == "你好"
    assert messages[2].content == "好的"
    assert isinstance(messages[3], HumanMessage) and messages[3].content == "在吗"


async def test_llm_error_yields_error_event(client):
    fake = FakeChatModel(chunks=[], error=RuntimeError("模型炸了"))
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
