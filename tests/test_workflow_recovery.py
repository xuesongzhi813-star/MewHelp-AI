from contextlib import aclosing

from app.history import InMemorySessionStore
from app.workflow.runtime import open_checkpointer, WorkflowRuntime
from tests.test_workflow_graph import Gateway
from tests.test_workflow_chat import make_runtime


async def test_reopen_completed_thread_continues_without_duplicate_history(tmp_path):
    path = str(tmp_path / 'reopen.sqlite')
    store = InMemorySessionStore(10)
    async with open_checkpointer(path) as saver:
        runtime = await make_runtime(saver, store)
        _ = [e async for e in runtime.stream_turn('s', '你好', model=Gateway('闲聊'))]
    async with open_checkpointer(path) as saver:
        runtime = await make_runtime(saver, store)
        _ = [e async for e in runtime.stream_turn('s', '谢谢', model=Gateway('闲聊'))]
        state = await runtime.get_state('s')
        assert [m.content for m in state['messages'] if m.type == 'human'] == ['你好', '谢谢']
        assert len(store.get_history('s')) == 4


async def test_no_stale_evidence_or_budget_on_next_turn(tmp_path):
    async with open_checkpointer(str(tmp_path / 'reset.sqlite')) as saver:
        runtime = await make_runtime(saver)
        _ = [e async for e in runtime.stream_turn('s', '投诉', model=Gateway('投诉'))]
        first = await runtime.get_state('s')
        _ = [e async for e in runtime.stream_turn('s', '你好', model=Gateway('闲聊'))]
        state = await runtime.get_state('s')
        assert state['retrieval'] is None and state['turn_action_ids'] == []
        assert state['budget']['tool_calls'] == 0 and state['actions'] == first['actions']
        assert state['node_trace'] == ['resolve_reference', 'classify_intent', 'chat_reply', 'log_result']


async def test_interrupted_turn_not_replayed(tmp_path):
    async with open_checkpointer(str(tmp_path / 'cancel.sqlite')) as saver:
        runtime = await make_runtime(saver)
        async with aclosing(runtime.stream_turn('s', '订单', model=Gateway())) as stream:
            first = await anext(stream)
            while first['type'] == 'turn':
                first = await anext(stream)
            assert first['type'] == 'token'
        assert (await runtime.get_state('s'))['status'] == 'interrupted'
        _ = [e async for e in runtime.stream_turn('s', '你好', model=Gateway('闲聊'))]
        state = await runtime.get_state('s')
        assert state['status'] == 'completed'
        assert state['intent'] == '闲聊'


async def test_interrupted_tool_pair_not_sent_to_next_model(tmp_path):
    from tests.test_agent_loop import call
    def broken(*args, **kwargs):
        raise ConnectionError('tool unavailable')
    async with open_checkpointer(str(tmp_path / 'orphan.sqlite')) as saver:
        runtime = WorkflowRuntime(saver, InMemorySessionStore(10), gateway_factory=lambda model:model, execute=broken)
        _ = [e async for e in runtime.stream_turn('s', '订单1001', model=Gateway(rounds=[[call('query_order', {'order_id':'1001'})]]))]
        assert (await runtime.get_state('s'))['status'] == 'interrupted'
        gateway = Gateway()
        _ = [e async for e in runtime.stream_turn('s', '再试一次', model=gateway)]
        messages = gateway.inputs[0]
        call_ids = {call['id'] for m in messages if m.type == 'ai' for call in m.tool_calls}
        result_ids = {m.tool_call_id for m in messages if m.type == 'tool'}
        assert call_ids == result_ids
