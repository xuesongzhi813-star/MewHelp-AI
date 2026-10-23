from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from app.workflow.graph import build_workflow
from app.workflow.agent import new_budget
from tests.test_workflow_graph import Gateway, dependencies


async def test_missing_order_has_real_interrupt_and_resume_does_not_reclassify():
    gateway = Gateway('退款退货')
    deps, seen, _ = dependencies(gateway)
    import json
    from app.demo_orders import get_demo_order
    def execute(name, args, **kwargs):
        seen.append(name)
        return json.dumps(get_demo_order(args['order_id']))
    deps.execute = execute
    async def expand(messages, budget):
        question = json.loads(messages[-1].content)['question']
        return {'queries':[question+'退货条件', question+'退货限制']}
    gateway.expand = expand
    graph = build_workflow(checkpointer=InMemorySaver(), deps=deps)
    config = {'configurable': {'thread_id':'missing-order'}}
    await graph.ainvoke({'messages':[HumanMessage(content='这个能退吗', id='u')],
        'raw_question':'这个能退吗', 'request_id':'r', 'session_id':'s', 'conversation_id':42,
        'budget':new_budget().model_dump(), 'actions':{}, 'node_trace':[]}, config)
    snapshot = await graph.aget_state(config)
    assert snapshot.interrupts
    assert snapshot.next == ('await_order',)
    assert seen == []
    selection = snapshot.values['selection']
    state = await graph.ainvoke(Command(resume={'selection_id':selection['selection_id'], 'order_id':'1001'},
        update={'budget':new_budget().model_dump()}), config)
    assert state['node_trace'].count('classify_intent') == 1
    assert state['order']['order_id'] == '1001'
    assert seen[0] == 'query_order'
    assert state['node_trace'].index('read_order') < state['node_trace'].index('expand_queries') < state['node_trace'].index('retrieve_policy')
