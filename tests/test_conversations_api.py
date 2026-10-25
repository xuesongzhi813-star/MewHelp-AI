import json
import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from app.main import create_app
from app.history import InMemorySessionStore
from app.context.store import InMemoryConversationStore
from app.workflow.runtime import WorkflowRuntime
from tests.test_workflow_graph import Gateway
from app.dependencies import get_chat_model


@pytest.fixture
async def api():
    app=create_app(); db=InMemoryConversationStore()
    runtime=WorkflowRuntime(InMemorySaver(),InMemorySessionStore(100),context_store=db,gateway_factory=lambda m:Gateway('闲聊'))
    app.state.workflow_runtime=runtime
    app.dependency_overrides[get_chat_model]=lambda:Gateway('闲聊')
    from app.config import get_settings
    old=get_settings().chat_workflow_enabled
    get_settings().chat_workflow_enabled=True
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
        try:
            yield c,db,runtime
        finally:
            get_settings().chat_workflow_enabled=old


async def test_browser_group_lists_raw_history_and_no_cross_user(api):
    c,db,runtime=api
    user='a'*32
    ids=[]
    for key in ('tmp-a','tmp-b'):
        response=await c.post('/chat',json=dict(session_id=key,user_id=user,message='你好原文'))
        events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        assert events[0]['type']=='conversation' and events[0]['user_id']==user
        ids.append(events[0]['conversation_id'])
    response=await c.get('/api/conversations',params={'user_id':user})
    assert response.status_code==200
    assert [r['id'] for r in response.json()['items']]==ids[::-1]
    response=await c.get(f'/api/conversations/{ids[0]}/messages',params={'user_id':user})
    assert [r['role'] for r in response.json()['messages']]==['user','assistant']
    assert response.json()['messages'][0]['content']=='你好原文'
    assert (await c.get(f'/api/conversations/{ids[0]}/messages',params={'user_id':'b'*32})).status_code==404
    assert (await c.get('/api/conversations/999999/messages',params={'user_id':user})).status_code==404
    assert len(db.identities)==2
    assert (await c.get('/api/conversations',params={'user_id':'bad'})).status_code==422


async def test_same_temporary_key_singleflight_and_explicit_mismatch(api):
    import asyncio
    c,db,runtime=api; user='b'*32
    identities=await asyncio.gather(*(runtime.resolve_identity('temp',user_id=user) for _ in range(5)))
    assert len(db.identities)==1
    sid=identities[0].session_id
    assert all(i.session_id==sid for i in identities)
    response=await c.post('/chat',json=dict(session_id=sid,user_id='c'*32,message='x'))
    assert response.status_code==422
    assert len(db.identities)==1
