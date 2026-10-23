import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.history import InMemorySessionStore
from app.workflow.agent import new_budget
from app.workflow.graph import build_workflow, WorkflowDeps
from app.workflow.knowledge import dump_retrieval
from tests.test_workflow_knowledge import result
from tests.test_agent_loop import call


class Gateway:
    def __init__(self, intent='订单', rounds=None):
        self.intent, self.rounds = intent, list(rounds or [[call('respond_to_user')]])
        self.answers = 0
        self.inputs = []

    async def classify(self, messages, budget):
        return self.intent

    async def decide(self, messages, budget):
        budget.decision_calls += 1
        self.inputs.append(messages)
        return AIMessage(content='', tool_calls=self.rounds.pop(0))

    async def stream_answer(self, messages, budget):
        self.answers += 1
        yield AIMessageChunk(content='依据查询结果回答。[1]')

    def query_adapter(self, budget):
        class Adapter:
            def invoke(self, messages):
                return {'normalized_query': '退货', 'intent': 'knowledge'}
        return Adapter()


def dependencies(gateway, *, evidence=None):
    seen, pool = [], []
    def retrieve(question, **kwargs):
        seen.append('retrieve')
        return evidence if evidence is not None else result(.9)
    def execute(name, args, **kwargs):
        seen.append(name)
        return '结果'
    def record(*args):
        pool.append(args)
    deps = WorkflowDeps(gateway=gateway, store=InMemorySessionStore(10), retrieve=retrieve, execute=execute, record_low_confidence=record)
    return deps, seen, pool


async def invoke(gateway, *, evidence=None):
    deps, seen, pool = dependencies(gateway, evidence=evidence)
    graph = build_workflow(checkpointer=InMemorySaver(), deps=deps)
    state = await graph.ainvoke({'messages': [HumanMessage(content='订单1001能退吗')], 'raw_question': '订单1001能退吗',
                                'conversation_id': 42, 'budget': new_budget().model_dump(), 'actions': {}, 'node_trace': []},
                               {'configurable': {'thread_id': 'test'}, 'recursion_limit': 64})
    return state, seen, pool


async def test_policy_forces_retrieve_before_agent():
    state, seen, _ = await invoke(Gateway('退款退货'))
    assert seen == ['retrieve']
    trace = state['node_trace']
    assert trace.index('retrieve_knowledge') < trace.index('evidence_gate') < trace.index('agent_decide')
    assert trace[-1] == 'log_result'


async def test_return_policy_can_then_query_order():
    state, seen, _ = await invoke(Gateway('退款退货', [[call('query_order', {'order_id':'1001'})], [call('respond_to_user')]]))
    assert seen == ['retrieve', 'query_order']
    assert state['budget']['tool_calls'] == 1


async def test_business_skips_retrieval_and_gate():
    state, seen, _ = await invoke(Gateway())
    assert seen == []
    assert 'evidence_gate' not in state['node_trace']


async def test_low_evidence_records_once_and_never_calls_agent():
    gateway = Gateway('商品咨询')
    state, seen, pool = await invoke(gateway, evidence=result(hits=False))
    assert gateway.answers == 0 and gateway.inputs == []
    assert len(pool) == 1 and pool[0][1] == 42 and pool[0][2] == 'retrieval_low_conf'
    assert 'agent_decide' not in state['node_trace']


async def test_complaint_two_actions_zero_agent_zero_ticket():
    gateway = Gateway('投诉')
    state, seen, _ = await invoke(gateway)
    assert {a['kind'] for a in state['actions'].values()} == {'handoff', 'create_ticket'}
    assert seen == [] and gateway.inputs == [] and gateway.answers == 0


async def test_chat_one_classifier_zero_answer_model():
    gateway = Gateway('闲聊')
    state, seen, _ = await invoke(gateway)
    assert state['final_text'].startswith('您好')
    assert gateway.inputs == [] and gateway.answers == 0 and seen == []


async def test_graph_order_then_logistics():
    gateway = Gateway(rounds=[[call('query_order', {'order_id': '1001'})], [call('query_logistics', {'order_id': '1001'})], [call('respond_to_user')]])
    state, seen, _ = await invoke(gateway)
    assert seen == ['query_order', 'query_logistics']
    assert state['budget']['tool_calls'] == 2


async def test_foreign_control_never_executes_ticket():
    state, seen, _ = await invoke(Gateway(rounds=[[call('create_ticket', {'description':'投诉'})], [call('respond_to_user')]]))
    assert seen == []
    assert any(a['kind'] == 'create_ticket' for a in state['actions'].values())


async def test_preloaded_policy_evidence_is_not_retrieved_twice():
    gateway = Gateway('退款退货', [[call('query_faq', {'keyword':'退货流程'})], [call('respond_to_user')]])
    state, seen, _ = await invoke(gateway)
    assert seen == ['retrieve']
    assert state['retrieval']['hits'][0]['chunk_id'] == 1


async def test_business_faq_rejected_evidence_never_reaches_model():
    from app.workflow.nodes import model_messages
    for score in (.01, float('nan')):
        gateway = Gateway('订单', [[call('query_faq', {'keyword':'退货'})], [call('respond_to_user')]])
        state, seen, _ = await invoke(gateway, evidence=result(score))
        assert seen == ['retrieve']
        assert not any(m.additional_kwargs.get('workflow_evidence') for m in gateway.inputs[1])
        assert not any(m.additional_kwargs.get('workflow_evidence') for m in model_messages(state))
