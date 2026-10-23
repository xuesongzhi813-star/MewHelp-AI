"""Meter every LLM path; do not leak raw control data into final text."""
import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from app.workflow.budget import BudgetExceeded
from app.workflow.contracts import BudgetState
from app.workflow.models import ModelGateway


class Provider:
    def __init__(self):
        self.calls = []
        self.schema = None
        self.parsed = {"intent": "订单"}
        self.kwargs = {}

    def bind_tools(self, tools, **kwargs):
        self.tools = tools
        self.kwargs = kwargs
        return self

    def with_structured_output(self, schema, **kwargs):
        self.schema = schema
        assert kwargs["include_raw"] is True
        return self

    def invoke(self, messages, **kwargs):
        self.calls.append(kwargs)
        return {"raw": AIMessage(content="", usage_metadata={"input_tokens": 20, "output_tokens": 10, "total_tokens": 30}), "parsed": self.parsed, "parsing_error": None}

    async def ainvoke(self, messages, **kwargs):
        return self.invoke(messages, **kwargs)

    async def astream(self, messages, **kwargs):
        self.calls.append(kwargs)
        yield AIMessageChunk(content="您好")
        yield AIMessageChunk(content="！", usage_metadata={"input_tokens": 20, "output_tokens": 5, "total_tokens": 25})


async def test_classification_and_query_usage_are_charged():
    provider = Provider()
    gateway = ModelGateway(provider)
    budget = BudgetState()
    assert await gateway.classify([HumanMessage(content="查订单")], budget) == "订单"
    provider.parsed = {"normalized_query": "退货政策", "intent": "knowledge", "reason": "保真"}
    result = gateway.query_adapter(budget).invoke([HumanMessage(content="退货政策")])
    assert result.normalized_query == "退货政策"
    assert budget.charged_tokens == 60
    assert budget.actual_calls == 2
    assert all(call["max_tokens"] > 0 for call in provider.calls)


async def test_exhausted_budget_never_calls_provider():
    provider = Provider()
    with pytest.raises(BudgetExceeded):
        await ModelGateway(provider).classify([HumanMessage(content="查订单")], BudgetState(token_limit=1))
    assert provider.calls == []


async def test_stream_usage_charged_once():
    provider = Provider()
    budget = BudgetState()
    chunks = [chunk async for chunk in ModelGateway(provider).stream_answer([HumanMessage(content="你好")], budget)]
    assert "".join(chunk.content for chunk in chunks) == "您好！"
    assert budget.charged_tokens == 25
    assert budget.actual_calls == 1
    assert provider.kwargs["tool_choice"] == "none"


async def test_invalid_intent_is_not_silently_chat():
    provider = Provider()
    provider.parsed = {"intent": "任意出口"}
    with pytest.raises(ValueError):
        await ModelGateway(provider).classify([], BudgetState())


async def test_provider_failure_keeps_conservative_charge():
    class Broken(Provider):
        async def ainvoke(self, messages, **kwargs):
            raise RuntimeError("test outage")
    budget = BudgetState()
    with pytest.raises(RuntimeError):
        await ModelGateway(Broken()).classify([], budget)
    assert budget.charged_tokens > 0


def test_workflow_model_has_no_sdk_retries():
    from app.config import Settings
    from app.llm import build_workflow_model
    model = build_workflow_model(Settings(_env_file=None))
    assert model.max_retries == 0
    assert model.request_timeout == 90


async def test_real_chatopenai_structured_path_passes_output_cap():
    import json
    import httpx
    from langchain_openai import ChatOpenAI

    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "test", "object": "chat.completion", "model": "test", "created": 0,
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "intent", "type": "function", "function": {
                    "name": "IntentDecision", "arguments": '{"intent":"订单"}'}}]}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        model = ChatOpenAI(model="test", api_key="sk-test", base_url="https://test.invalid/v1", http_async_client=client, max_retries=0)
        budget = BudgetState()
        assert await ModelGateway(model).classify([HumanMessage(content="订单1001")], budget) == "订单"
    assert requests[0].get("max_completion_tokens", requests[0].get("max_tokens")) == 128
    assert budget.charged_tokens == 30
