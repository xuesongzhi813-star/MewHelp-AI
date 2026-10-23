"""Characterize the pinned saver/graph boundary needed for order selection."""
from langgraph.graph import START, END, StateGraph
from langgraph.types import interrupt, Command
from app.workflow.runtime import open_checkpointer
from app.workflow.contracts import WorkflowState


async def test_sqlite_interrupt_resumes_same_node_after_reopen_without_replaying_prepare(tmp_path):
    visits = []
    def build(saver):
        graph = StateGraph(WorkflowState)
        def prepare(state):
            visits.append('prepare')
            return {'pending_decision': {'selection_id': 'fixed-selection'}, 'status': 'awaiting_order'}
        def select(state):
            visits.append('select')
            selected = interrupt(state['pending_decision'])
            return {'final_text': selected['order_id']}
        graph.add_node('prepare', prepare)
        graph.add_node('select', select)
        graph.add_edge(START, 'prepare')
        graph.add_edge('prepare', 'select')
        graph.add_edge('select', END)
        return graph.compile(checkpointer=saver)
    path = str(tmp_path / 'orders.sqlite')
    config = {'configurable': {'thread_id':'synthetic-order-session'}}
    async with open_checkpointer(path) as saver:
        graph = build(saver)
        events = [event async for event in graph.astream({'messages':[]}, config, stream_mode=['custom','updates'])]
        snapshot = await graph.aget_state(config)
        assert snapshot.next == ('select',)
        assert snapshot.interrupts[0].value == {'selection_id': 'fixed-selection'}
        assert any(kind == 'updates' and '__interrupt__' in data for kind, data in events)
        await graph.ainvoke(Command(update={'actions': {'a': {'status': 'completed'}}}), config)
        updated = await graph.aget_state(config)
        assert updated.interrupts
        assert updated.values['actions']['a']['status'] == 'completed'
    async with open_checkpointer(path) as saver:
        graph = build(saver)
        result = await graph.ainvoke(Command(resume={'order_id':'1001'}, update={'status':'running'}), config)
        assert result['final_text'] == '1001'
        assert result['status'] == 'running'
        assert visits == ['prepare','select','select','select']
        assert not (await graph.aget_state(config)).interrupts
