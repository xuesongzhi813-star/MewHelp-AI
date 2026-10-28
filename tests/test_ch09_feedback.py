from types import SimpleNamespace

import httpx
import pytest


@pytest.fixture
async def feedback_app(tmp_path):
    from app.main import create_app
    from app.history import InMemorySessionStore
    from app.workflow.runtime import WorkflowRuntime, open_checkpointer
    from app.api.feedback import get_event_recorder
    from tests.test_workflow_graph import Gateway
    from tests.test_workflow_knowledge import result
    app = create_app()
    async with open_checkpointer(str(tmp_path/'feedback.sqlite')) as saver:
        runtime = WorkflowRuntime(saver, InMemorySessionStore(10), gateway_factory=lambda model:model,
            retrieve=lambda *a,**kw:result(.9), record=lambda *a: None)
        app.state.workflow_runtime = runtime
        first = [e async for e in runtime.stream_turn('s','七天之后能退货吗',model=Gateway('商品咨询'))]
        turn = [e for e in first if e['type']=='turn'][-1]
        _ = [e async for e in runtime.stream_turn('s','你好',model=Gateway('闲聊'))]
        events = []
        def record(event):
            if not events:
                events.append(event)
                return SimpleNamespace(id=7, created=True)
            return SimpleNamespace(id=7, created=False)
        app.dependency_overrides[get_event_recorder] = lambda: record
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            yield client, runtime, turn, events


async def test_feedback_recovers_the_evaluated_old_turn_and_deduplicates(feedback_app):
    client, runtime, turn, events = feedback_app
    body = {k:turn[k] for k in ('session_id','user_message_id','assistant_message_id')}
    body['rating'] = 'down'
    first = await client.post('/api/feedback',json=body)
    second = await client.post('/api/feedback',json=body)
    assert first.status_code == second.status_code == 200
    assert first.json()['event_id'] == second.json()['event_id'] == 7
    assert len(events) == 1
    assert events[0].raw_question == '七天之后能退货吗'
    assert events[0].identity.user_message_id == turn['user_message_id']
    assert events[0].snapshot.hits and events[0].snapshot.snapshot_status == 'retrieved'


async def test_feedback_cannot_submit_own_question_or_cross_turn_message(feedback_app):
    client, runtime, turn, events = feedback_app
    body = {k:turn[k] for k in ('session_id','user_message_id','assistant_message_id')}
    assert (await client.post('/api/feedback',json={**body,'rating':'down','raw_question':'伪造'})).status_code == 422
    assert (await client.post('/api/feedback',json={**body,'rating':'down','assistant_message_id':999999})).status_code == 404
    assert not events


async def test_missing_checkpoints_still_collects_question_with_empty_snapshot(feedback_app, monkeypatch):
    client, runtime, turn, events = feedback_app
    async def missing(*args,**kwargs):
        raise RuntimeError('checkpoint unavailable')
    from app.flywheel import snapshots
    monkeypatch.setattr(snapshots, '_history_snapshot', missing)
    body = {k:turn[k] for k in ('session_id','user_message_id','assistant_message_id')}
    response = await client.post('/api/feedback',json={**body,'rating':'down'})
    assert response.status_code == 200
    assert events[0].snapshot.hits == []
    assert events[0].snapshot.snapshot_status == 'snapshot_unavailable'


async def test_feedback_db_failure_returns_retryable_error_without_private_details(feedback_app):
    from app.api.feedback import get_event_recorder
    client, runtime, turn, events = feedback_app
    def broken(event):
        raise ConnectionError('synthetic-private-database-value')
    client._transport.app.dependency_overrides[get_event_recorder] = lambda: broken
    body = {k:turn[k] for k in ('session_id','user_message_id','assistant_message_id')}
    response = await client.post('/api/feedback',json={**body,'rating':'down'})
    assert response.status_code == 503
    assert 'synthetic-private' not in response.text


async def test_refusal_preserves_snapshot_through_legacy_callback_protocol(tmp_path):
    from app.history import InMemorySessionStore
    from app.workflow.runtime import WorkflowRuntime, open_checkpointer
    from app.flywheel.store import current_event
    from tests.test_workflow_graph import Gateway
    from tests.test_workflow_knowledge import result
    seen = []
    def collect(question, conversation, source, reason):
        seen.append(current_event())
    async with open_checkpointer(str(tmp_path/'refuse.sqlite')) as saver:
        runtime = WorkflowRuntime(saver, InMemorySessionStore(10), gateway_factory=lambda model:model,
            retrieve=lambda *a,**kw:result(.1), record=collect)
        events = [e async for e in runtime.stream_turn('s','知识不足的问题',model=Gateway('商品咨询'))]
    assert seen and seen[0] is not None
    assert seen[0].source == 'retrieval_low_conf'
    assert seen[0].snapshot.hits[0]['rerank_score'] == .1
    assert seen[0].snapshot.hits[0]['score'] == .8
    assert events[-1]['type'] == 'done'


async def test_pool_failure_does_not_suppress_refusal_answer(tmp_path):
    from app.history import InMemorySessionStore
    from app.workflow.runtime import WorkflowRuntime, open_checkpointer
    from tests.test_workflow_graph import Gateway
    from tests.test_workflow_knowledge import result
    def broken(*args):
        raise ConnectionError('synthetic-private')
    async with open_checkpointer(str(tmp_path/'pool-failure.sqlite')) as saver:
        runtime = WorkflowRuntime(saver, InMemorySessionStore(10), gateway_factory=lambda model:model,
            retrieve=lambda *a,**kw:result(.1), record=broken)
        events = [e async for e in runtime.stream_turn('s','知识不足的问题',model=Gateway('商品咨询'))]
    assert any(e['type']=='token' and '现有知识不足' in e['content'] for e in events)
    assert any(e['type']=='collection_status' and e['saved'] is False for e in events)
    assert events[-1]['type'] == 'done'
