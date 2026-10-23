"""Bare agent: observations drive the next tool and writes never run."""
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from app.workflow.agent import run_agent_loop
from app.workflow.contracts import BudgetState
from app.workflow.models import ModelGateway


def call(name, args=None, id="call1"):
    return {"name": name, "args": args or {}, "id": id}


async def test_bare_faq_rewrite_is_metered_without_legacy_execution(monkeypatch):
    from tests.test_workflow_models import Provider
    from tests.test_workflow_knowledge import result as knowledge_result
    class FAQProvider(Provider):
        def __init__(self):
            super().__init__()
            self.parsed = {'normalized_query':'退货', 'intent':'knowledge'}
            self.rounds = [[call('query_faq', {'keyword':'退货'})], [call('respond_to_user')]]
        async def ainvoke(self, messages, **kwargs):
            return AIMessage(content='', tool_calls=self.rounds.pop(0),
                             usage_metadata={'input_tokens':20, 'output_tokens':10, 'total_tokens':30})
    plans, legacy = [], []
    def retrieve(question, *, plan):
        plans.append(plan)
        return knowledge_result(.9)
    monkeypatch.setattr('app.kb.retriever.retrieve_result', retrieve)
    provider = FAQProvider()
    result = await run_agent_loop('退货', gateway=ModelGateway(provider), context={},
                                  execute=lambda *a, **kw: legacy.append(a) or '旧检索结果')
    assert legacy == []
    assert len(plans) == 1 and plans[0].normalized_query == '退货'
    assert result['usage']['actual_calls'] == 4
    assert result['usage']['charged_tokens'] == 115


async def test_bare_faq_exhausted_rewrite_cannot_call_provider_or_retrieve(monkeypatch):
    from tests.test_workflow_models import Provider
    provider = Provider()
    class ExhaustingGateway(ModelGateway):
        async def decide(self, messages, budget):
            budget.charged_tokens = budget.token_limit
            return AIMessage(content='', tool_calls=[call('query_faq', {'keyword':'退货'})])
    executed = []
    result = await run_agent_loop('退货', gateway=ExhaustingGateway(provider), context={},
                                  execute=lambda *a, **kw: executed.append(a) or '旧结果')
    assert result['stop_reason'] == 'budget'
    assert provider.calls == [] and executed == []


class ScriptedProvider:
    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.received = []

    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, messages, **kwargs):
        self.received.append(list(messages))
        return AIMessage(content="", tool_calls=self.rounds.pop(0))

    async def astream(self, messages, **kwargs):
        self.received.append(list(messages))
        yield AIMessageChunk(content="已核实")


async def run(rounds, *, budget=None):
    provider = ScriptedProvider(rounds)
    executed = []

    def execute(name, args, **kwargs):
        executed.append((name, args))
        return "订单已发货" if name == "query_order" else "物流运输中"

    result = await run_agent_loop("订单1001的状态和物流", gateway=ModelGateway(provider), execute=execute,
                                  context={"conversation_id": 1}, budget=budget)
    return result, provider, executed


async def test_order_then_logistics_receives_first_result():
    result, provider, executed = await run([
        [call("query_order", {"order_id": "1001"}, "order")],
        [call("query_logistics", {"order_id": "1001"}, "logistics")],
        [call("respond_to_user")],
    ])
    assert [name for name, _ in executed] == ["query_order", "query_logistics"]
    assert any(isinstance(message, ToolMessage) and message.content == "订单已发货" for message in provider.received[1])
    assert result["answer"] == "已核实"
    assert result["tool_names"] == ["query_order", "query_logistics"]


async def test_one_lookup_then_answer():
    result, _, executed = await run([[call("query_order", {"order_id": "1001"})], [call("respond_to_user")]])
    assert len(executed) == 1
    assert result["stop_reason"] == "answer"


async def test_missing_order_id_clarifies_without_tool():
    result, _, executed = await run([[call("respond_to_user", {"response_mode": "clarify"})]])
    assert executed == []
    assert result["stop_reason"] == "clarify"


async def test_create_ticket_is_suggestion_only():
    result, _, executed = await run([[call("create_ticket", {"description": "投诉", "ticket_type": "投诉"})], [call("respond_to_user")]])
    assert executed == []
    assert result["actions"][0]["kind"] == "create_ticket"


async def test_multiple_tool_calls_get_paired_results():
    _, provider, executed = await run([[call("query_order", {"order_id": "1001"}, "a"), call("query_logistics", {"order_id": "1001"}, "b")], [call("respond_to_user")]])
    assert len(executed) == 1
    paired = [m for m in provider.received[1] if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in paired] == ["a", "b"]
    assert "未执行" in paired[1].content


async def test_unknown_tool_is_never_executed():
    _, _, executed = await run([[call("arbitrary_shell")], [call("respond_to_user")]])
    assert executed == []


async def test_six_calls_or_ten_decisions_stop_without_extra_model():
    result, provider, executed = await run([[call("query_order", {"order_id": "1001"}, f"t{i}")] for i in range(7)])
    assert len(executed) == 6
    assert len(provider.received) == 6
    assert result["stop_reason"] == "budget"
    # Isolate the decision count from the independent estimated-token ceiling.
    result, provider, executed = await run([[call("unknown", id=f"t{i}")] for i in range(11)], budget=BudgetState(token_limit=100000))
    assert executed == []
    assert len(provider.received) == 10
    assert result["stop_reason"] == "budget"
