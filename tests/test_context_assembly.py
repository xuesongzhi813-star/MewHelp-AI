from dataclasses import asdict
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from app.config import get_settings
from app.context.assembly import build_agent_context, fixed_agent_schemas, bounded_observation
from app.context.contracts import SummaryRow
from app.context.tokens import estimate_text


def state():
    messages=[HumanMessage('早先问题',id='db:21'),AIMessage('答'*80,id='assistant:21'),
              HumanMessage('这单怎么说',id='db:41'),
              AIMessage('',id='call',additional_kwargs={'preloaded_order':True},tool_calls=[dict(name='query_order',args={'order_id':'1001'},id='x')]),
              ToolMessage('订单数据',id='tool',tool_call_id='x',additional_kwargs={'preloaded_order':True})]
    return dict(messages=messages,message_refs={m.id:dict(db_id=d,turn_from_msg_id=t) for m,d,t in
        zip(messages,[21,30,41,None,None],[21,21,41,41,41])},conversation_id=1,
        current_turn_from_msg_id=41,summary_upto_msg_id=14,layer1_from_msg_id=21,
        summary_rows=[asdict(SummaryRow(1,1,1,1,14,'订单1001未收到，想查物流。'))],
        raw_question='这单怎么说',order={'order_id':'1001','status':'已发货'},understanding={},retrieval=None)


def test_fixed_prefix_and_order_original_then_single_data_block():
    s=state(); settings=get_settings()
    messages=build_agent_context(s,settings=settings)
    assert [m.type for m in messages]==['system','human','ai','human','human']
    assert messages[2].content=='答'*80
    assert messages[3].content=='这单怎么说'
    assert 'summary:1' in messages[-1].content
    assert '1001' in messages[-1].content
    assert len(s['messages'])==5
    before=(messages[0].content,fixed_agent_schemas())
    s['summary_rows']=[]; s['order']=None
    assert (build_agent_context(s,settings=settings)[0].content,fixed_agent_schemas())==before


def test_bounded_tool_observation_is_explicit_and_artifact_independent():
    settings=get_settings().model_copy(update={'tool_result_max_tokens':80})
    content=bounded_observation('大'*2000,settings=settings)
    assert '摘录' in content and estimate_text(content)<=80


def test_oversized_evidence_metadata_cannot_silently_remove_all_evidence():
    import pytest
    from dataclasses import replace
    from app.context.assembly import dynamic_data
    from app.context.budget import ContextBudgetError
    from app.workflow.knowledge import dump_retrieval
    from tests.test_workflow_knowledge import result
    data=result(.9)
    data=replace(data,hits=(replace(data.hits[0],section_path='章'*3000),))
    with pytest.raises(ContextBudgetError):
        dynamic_data({'retrieval':dump_retrieval(data)},settings=get_settings())


async def test_new_budget_and_fixed_decide_answer_schemas(monkeypatch):
    from app.workflow.agent import new_budget
    from app.workflow.models import ModelGateway
    from tests.test_workflow_models import Provider
    provider=Provider(); provider.parsed={}
    class DecisionProvider(Provider):
        async def ainvoke(self,messages,**kwargs):
            return AIMessage('',tool_calls=[dict(name='respond_to_user',args={},id='x')])
    provider=DecisionProvider(); gateway=ModelGateway(provider); budget=new_budget(contextual=True)
    await gateway.decide([HumanMessage('你好')],budget)
    decision_schemas=provider.tools
    _=[c async for c in gateway.stream_answer([HumanMessage('你好')],budget)]
    assert decision_schemas==provider.tools==fixed_agent_schemas()
    assert provider.kwargs['tool_choice']=='none'
    assert budget.token_limit>12000
