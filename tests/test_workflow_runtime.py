import asyncio

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import StateGraph, START, END

from app.workflow.contracts import WorkflowState
from app.workflow.runtime import open_checkpointer, SessionLocks


def simple_graph(saver):
    graph = StateGraph(WorkflowState)
    graph.add_node('save', lambda state: {'status': 'completed'})
    graph.add_edge(START, 'save')
    graph.add_edge('save', END)
    return graph.compile(checkpointer=saver)


async def test_sqlite_reopens_saved_messages_and_actions(tmp_path):
    path = str(tmp_path / 'checkpoints.sqlite')
    config = {'configurable': {'thread_id': 'one'}}
    async with open_checkpointer(path) as saver:
        await simple_graph(saver).ainvoke({'messages': [HumanMessage(content='查询', id='user1')],
                                         'actions': {'a': {'kind': 'create_ticket', 'status': 'available'}}}, config)
    async with open_checkpointer(path) as saver:
        state = (await simple_graph(saver).aget_state(config)).values
        assert state['messages'][0].content == '查询'
        assert state['actions']['a']['status'] == 'available'


async def test_state_roundtrip_preserves_tool_message_pair(tmp_path):
    async with open_checkpointer(str(tmp_path / 'pair.sqlite')) as saver:
        graph = simple_graph(saver)
        config = {'configurable': {'thread_id': 'pair'}}
        await graph.ainvoke({'messages': [AIMessage(content='', tool_calls=[{'name': 'query_order', 'args': {'order_id': '1001'}, 'id': 'a'}]),
                                          ToolMessage(content='已发货', tool_call_id='a')]}, config)
        messages = (await graph.aget_state(config)).values['messages']
        assert messages[0].tool_calls[0]['id'] == messages[1].tool_call_id


async def test_same_session_serializes_different_sessions_overlap():
    locks = SessionLocks()
    seen = []
    async with locks.hold('a'):
        async def worker(key):
            async with locks.hold(key):
                seen.append(key)
        same = asyncio.create_task(worker('a'))
        other = asyncio.create_task(worker('b'))
        await other
        assert seen == ['b']
        assert not same.done()
    await same
    assert seen == ['b', 'a']
    assert locks.size == 0


async def test_waiting_lock_is_not_evicted():
    locks = SessionLocks()
    order = []
    async def worker(i):
        async with locks.hold('one'):
            order.append(i)
            await asyncio.sleep(0)
    await asyncio.gather(*(worker(i) for i in range(5)))
    assert order == [0, 1, 2, 3, 4]
    assert locks.size == 0


async def test_connection_closed_on_exit(tmp_path):
    async with open_checkpointer(str(tmp_path / 'close.sqlite')) as saver:
        connection = saver.conn
    try:
        await connection.execute('SELECT 1')
    except (ValueError, RuntimeError):
        pass
    else:
        raise AssertionError('SQLite connection leaked')
