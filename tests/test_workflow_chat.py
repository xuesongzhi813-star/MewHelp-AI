import asyncio
from contextlib import aclosing

from langchain_core.messages import AIMessageChunk

from app.history import InMemorySessionStore
from app.workflow.runtime import open_checkpointer, WorkflowRuntime
from tests.test_workflow_graph import Gateway


async def make_runtime(saver, store=None):
    return WorkflowRuntime(saver, store or InMemorySessionStore(10), gateway_factory=lambda model: model)


async def test_first_token_arrives_before_model_release(tmp_path):
    release = asyncio.Event()
    class Streaming(Gateway):
        async def stream_answer(self, messages, budget):
            yield AIMessageChunk(content='首字')
            await release.wait()
            yield AIMessageChunk(content='尾字')
    async with open_checkpointer(str(tmp_path / 'first.sqlite')) as saver:
        runtime = await make_runtime(saver)
        async with aclosing(runtime.stream_turn('one', '订单', model=Streaming())) as stream:
            first = await asyncio.wait_for(anext(stream), 2)
            assert first == {'type': 'token', 'content': '首字'}
            assert not release.is_set()
            release.set()
            rest = [event async for event in stream]
            assert rest[-1] == {'type': 'done'}


async def test_classification_and_tool_json_never_streamed(tmp_path):
    async with open_checkpointer(str(tmp_path / 'clean.sqlite')) as saver:
        runtime = await make_runtime(saver)
        events = [e async for e in runtime.stream_turn('one', '订单', model=Gateway())]
        assert ''.join(e['content'] for e in events if e['type'] == 'token') == '依据查询结果回答。[1]'


async def test_error_has_no_done_or_raw_exception(tmp_path):
    class Broken(Gateway):
        async def stream_answer(self, messages, budget):
            yield AIMessageChunk(content='尚未完成')
            raise RuntimeError('private-connection-string')
    store = InMemorySessionStore(10)
    async with open_checkpointer(str(tmp_path / 'error.sqlite')) as saver:
        runtime = await make_runtime(saver, store)
        events = [e async for e in runtime.stream_turn('one', '订单', model=Broken())]
        assert events[-1]['type'] == 'error'
        assert all(e['type'] not in {'done', 'actions'} for e in events)
        assert 'private-' not in str(events)
        assert [m.type for m in store.get_history('one')] == ['human']
        assert (await runtime.get_state('one'))['status'] == 'interrupted'


async def test_actions_not_enabled_on_error(tmp_path):
    class FailingStore(InMemorySessionStore):
        def append_message(self, session_id, message):
            if message.type == 'ai':
                raise RuntimeError('archive failed')
            super().append_message(session_id, message)
    async with open_checkpointer(str(tmp_path / 'archive.sqlite')) as saver:
        runtime = await make_runtime(saver, FailingStore(10))
        events = [e async for e in runtime.stream_turn('one', '投诉', model=Gateway('投诉'))]
        assert events[-1]['type'] == 'error'
        assert all(e['type'] != 'actions' for e in events)
        assert all(a['status'] != 'available' for a in (await runtime.get_state('one'))['actions'].values())
