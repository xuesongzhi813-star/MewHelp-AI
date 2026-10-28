import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.flywheel.store import current_event
from app.kb.evidence import EvidencePolicy
from app.workflow.budget import BudgetExceeded
from app.workflow.graph import build_workflow
from tests.test_agent_loop import call
from tests.test_workflow_graph import Gateway, dependencies
from tests.test_workflow_knowledge import result


class CheckedGateway(Gateway):
    def __init__(self, *, decision=True, error=None, intent='商品咨询', rounds=None):
        super().__init__(intent, rounds)
        self.decision, self.error = decision, error
        self.checks = []

    async def check_sufficiency(self, messages, budget):
        assert self.answers == 0
        self.checks.append(messages)
        if self.error:
            raise self.error
        return {'can_answer': self.decision, 'reason': '样例证据判断'}


async def run(gateway, *, evidence=None, policy=None):
    deps, seen, _ = dependencies(gateway, evidence=evidence)
    events = []
    deps.record_low_confidence = lambda *args: events.append(current_event())
    deps.evidence_policy = policy or EvidencePolicy('test', .5, (.8,.1,.1), .6, 'test')
    deps.self_check_enabled = True
    graph = build_workflow(checkpointer=InMemorySaver(), deps=deps)
    from app.workflow.agent import new_budget
    frames = []
    config = {'configurable': {'thread_id': 'case'}, 'recursion_limit':64}
    async for frame in graph.astream({'messages':[HumanMessage(content='衣服能机洗吗')],
        'raw_question':'衣服能机洗吗','session_id':'test','conversation_id':42,'request_id':'test',
        'budget':new_budget().model_dump(),'actions':{},'node_trace':[]}, config, stream_mode='custom'):
        frames.append(frame)
    return (await graph.aget_state(config)).values, frames, events


async def test_low_evidence_prevents_agent_and_saves_once():
    gateway=CheckedGateway()
    state,frames,events=await run(gateway,evidence=result(.9),
        policy=EvidencePolicy('test',.5,(.8,.1,.1),.85,'test'))
    assert not gateway.inputs and not gateway.checks and gateway.answers == 0
    assert len(events)==1 and events[0].source=='retrieval_low_conf'
    assert events[0].snapshot.hits[0]['answer']=='规则'
    assert events[0].snapshot.profile_version=='test'
    assert not any(f['type']=='citations' for f in frames)


async def test_self_check_false_saves_same_evidence_before_any_answer():
    gateway=CheckedGateway(decision=False)
    state,frames,events=await run(gateway)
    assert len(gateway.checks)==1 and gateway.answers==0 and not gateway.inputs
    assert len(events)==1 and events[0].source=='self_check'
    assert events[0].snapshot.hits[0]['rerank_score']==.9
    assert not any(f['type']=='citations' for f in frames)


async def test_true_then_agent_and_generation_with_formal_gate_evidence():
    gateway=CheckedGateway()
    # This policy accepts a score the legacy minimum may reject.
    state,frames,events=await run(gateway,evidence=result(.01),
        policy=EvidencePolicy('test',0.,(0.,1.,0.),.1,'test'))
    assert len(gateway.checks)==1 and gateway.answers==1 and not events
    assert any(m.additional_kwargs.get('workflow_evidence') for m in gateway.inputs[0])
    assert any(f['type']=='citations' for f in frames)


async def test_budget_failure_is_budget_reply_not_gap():
    gateway=CheckedGateway(error=BudgetExceeded('budget'))
    state,frames,events=await run(gateway)
    from app.workflow.agent import BUDGET_TEXT
    assert state['final_text']==BUDGET_TEXT and not events and gateway.answers==0


@pytest.mark.parametrize('error',[ValueError('invalid JSON'),ConnectionError('service unavailable')])
async def test_service_or_json_error_never_becomes_self_check_gap(error):
    gateway=CheckedGateway(error=error)
    with pytest.raises(type(error)):
        await run(gateway)
    assert gateway.answers==0 and not gateway.inputs


@pytest.mark.parametrize('intent',['闲聊','订单'])
async def test_business_and_chat_do_not_self_check(intent):
    gateway=CheckedGateway(intent=intent)
    await run(gateway)
    assert not gateway.checks


async def test_later_faq_tool_also_checks_before_next_agent_or_answer():
    gateway=CheckedGateway(intent='订单', decision=False,
        rounds=[[call('query_faq',{'keyword':'机洗'})],[call('respond_to_user')]])
    state,frames,events=await run(gateway)
    assert len(gateway.inputs)==1 and len(gateway.checks)==1 and gateway.answers==0
    assert len(events)==1 and events[0].source=='self_check'


async def test_invalid_provider_score_is_service_error_without_pool():
    gateway=CheckedGateway()
    with pytest.raises(ValueError,match='invalid_rerank_score'):
        await run(gateway,evidence=result(float('nan')))
    assert not gateway.inputs and not gateway.checks


async def test_gateway_check_is_metered_capped_and_strict():
    from app.workflow.models import ModelGateway
    from app.workflow.contracts import BudgetState
    from tests.test_workflow_models import Provider
    provider=Provider(); provider.parsed={'can_answer':True,'reason':'有明确证据'}
    budget=BudgetState()
    decision=await ModelGateway(provider).check_sufficiency([HumanMessage(content='测试')],budget)
    assert decision.can_answer and provider.calls[0]['max_tokens']==512
    assert budget.charged_tokens==30 and budget.actual_calls==1
    provider.parsed={'can_answer':'true','reason':'bad'}
    with pytest.raises(ValueError):
        await ModelGateway(provider).check_sufficiency([HumanMessage(content='测试')],BudgetState())


async def test_contextual_self_check_uses_exactly_the_bounded_generation_evidence(monkeypatch):
    from app.workflow.nodes import node_bodies
    from app.workflow.agent import new_budget
    from app.workflow.knowledge import dump_retrieval
    gateway=CheckedGateway()
    deps,_,_=dependencies(gateway)
    deps.self_check_enabled=True
    deps.evidence_policy=EvidencePolicy('test',.5,(.8,.1,.1),.6,'test')
    monkeypatch.setattr('app.context.assembly.dynamic_data', lambda state,settings: ({},[
        {'chunk_id':1,'text':'只保留实际呈现的证据','section_path':'节选'}]))
    await node_bodies(deps)['evidence_gate']({'current_turn_from_msg_id':1,'resolved_question':'问题',
        'retrieval':dump_retrieval(result(.9)),'budget':new_budget().model_dump()})
    import json
    data=json.loads(gateway.checks[0][-1].content)
    assert data['evidence'][0]['text']=='只保留实际呈现的证据'
