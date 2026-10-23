import json
import pytest
import httpx
from app.config import get_settings
from app.demo_orders import get_demo_order
from app.history import InMemorySessionStore
from app.kb.contracts import Hit, RetrievalResult
from app.schemas import SelectOrderRequest
from app.workflow.runtime import WorkflowRuntime, open_checkpointer
from app.workflow.actions import ActionError
from tests.test_workflow_graph import Gateway


class RefundGateway(Gateway):
    def __init__(self):
        super().__init__('退款退货')
        self.classifications = 0

    async def classify(self, messages, budget):
        self.classifications += 1
        return await super().classify(messages, budget)

    async def expand(self, messages, budget):
        question = json.loads(messages[-1].content)['question']
        return {'queries':[question+'退货条件',question+'退货流程']}


def runtime_for(saver):
    def execute(name, args, **kwargs):
        return json.dumps(get_demo_order(args['order_id']), ensure_ascii=False)
    def retrieve(question, *, plan, strategy):
        hit = Hit(1,.9,plan.filters.source_title,'退货','合成政策','文档章节',plan.filters.source_title+' > 条件')
        return RetrievalResult(plan,strategy,(hit,))
    return WorkflowRuntime(saver,InMemorySessionStore(10),gateway_factory=lambda model:model,
                           execute=execute,retrieve=retrieve,record=lambda *args:None)


@pytest.fixture(autouse=True)
def dense_strategy(monkeypatch):
    monkeypatch.setenv('KNOWLEDGE_STRATEGY','dense')
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def pause(runtime, gateway):
    events = [event async for event in runtime.stream_turn('s','这个能退吗',model=gateway)]
    assert events[-1] == {'type':'done'}
    return next(event for event in events if event['type']=='order_selection')


async def test_resume_restores_real_interrupt_after_restart_and_does_not_duplicate_user_message(tmp_path):
    path = str(tmp_path/'orders.sqlite')
    gateway = RefundGateway()
    async with open_checkpointer(path) as saver:
        runtime = runtime_for(saver)
        payload = await pause(runtime,gateway)
        store = runtime.store
    async with open_checkpointer(path) as saver:
        runtime = runtime_for(saver)
        runtime.store = store
        request = SelectOrderRequest(session_id='s',selection_id=payload['selection_id'],order_id='1001')
        events = [event async for event in runtime.stream_order_selection(request,model=gateway)]
        assert events[-1] == {'type':'done'}
        state = await runtime.get_state('s')
        assert state['order']['order_id']=='1001'
        assert state['selection']['status']=='consumed'
        assert state['status']=='completed'
        assert gateway.classifications == 1
        assert len([m for m in store.get_history('s') if m.type=='human']) == 1
        with pytest.raises(ActionError):
            await runtime.validate_order_selection(request)


async def test_old_selection_expires_after_new_chat_message(tmp_path):
    async with open_checkpointer(str(tmp_path/'expire.sqlite')) as saver:
        runtime = runtime_for(saver)
        payload = await pause(runtime,RefundGateway())
        _ = [event async for event in runtime.stream_turn('s','你好',model=Gateway('闲聊'))]
        with pytest.raises(ActionError):
            await runtime.validate_order_selection(SelectOrderRequest(session_id='s',selection_id=payload['selection_id'],order_id='1001'))


async def test_saving_other_action_does_not_consume_order_interrupt(tmp_path):
    async with open_checkpointer(str(tmp_path/'action.sqlite')) as saver:
        runtime=runtime_for(saver)
        payload=await pause(runtime,RefundGateway())
        async with runtime.locks.hold('s'):
            await runtime.save_actions('s',{'a':{'kind':'create_ticket','action_id':'a','status':'completed'}})
        restored=await runtime.public_session_state('s')
        assert restored['selection']['selection_id']==payload['selection_id']
        assert restored['actions'][0]['status']=='completed'


async def test_http_rejects_wrong_candidate_and_restores_public_selection(tmp_path):
    from app.main import create_app
    from app.dependencies import get_chat_model
    async with open_checkpointer(str(tmp_path/'http.sqlite')) as saver:
        runtime=runtime_for(saver)
        gateway=RefundGateway()
        payload=await pause(runtime,gateway)
        app=create_app()
        app.state.workflow_runtime=runtime
        app.dependency_overrides[get_chat_model]=lambda:gateway
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            response=await client.get('/api/chat/actions/state',params={'session_id':'s'})
            assert response.json()['selection']['selection_id']==payload['selection_id']
            response=await client.post('/api/chat/actions/order-selection',json={'session_id':'s','selection_id':payload['selection_id'],'order_id':'9999'})
            assert response.status_code==422
            assert 'messages' not in (await client.get('/api/chat/actions/state',params={'session_id':'s'})).json()
            body={'session_id':'s','selection_id':payload['selection_id'],'order_id':'1001'}
            response=await client.post('/api/chat/actions/order-selection',json=body)
            assert response.status_code==200
            assert '"type": "done"' in response.text
            assert '"type": "error"' not in response.text
            assert (await client.post('/api/chat/actions/order-selection',json=body)).status_code==409


async def test_concurrent_clicks_only_resume_once(tmp_path):
    import asyncio
    async with open_checkpointer(str(tmp_path/'concurrent.sqlite')) as saver:
        runtime=runtime_for(saver)
        gateway=RefundGateway()
        payload=await pause(runtime,gateway)
        request=SelectOrderRequest(session_id='s',selection_id=payload['selection_id'],order_id='1001')
        async def click():
            return [e async for e in runtime.stream_order_selection(request,model=gateway)]
        runs=await asyncio.gather(click(),click())
        assert sum(any(e['type']=='done' for e in run) for run in runs)==1
        assert sum(any(e['type']=='error' for e in run) for run in runs)==1
        assert gateway.classifications==1
