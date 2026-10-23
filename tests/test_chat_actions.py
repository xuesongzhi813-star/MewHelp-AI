import asyncio
from copy import deepcopy

import httpx
import pytest
from pydantic import ValidationError

from app.schemas import ConfirmTicketRequest
from app.workflow.actions import confirm_ticket, ActionError
from app.workflow.runtime import SessionLocks, open_checkpointer
from tests.test_workflow_chat import make_runtime
from tests.test_workflow_graph import Gateway


class Runtime:
    def __init__(self):
        self.locks = SessionLocks()
        self.actions = {'a': {'action_id':'a', 'kind':'create_ticket', 'status':'available', 'description':'服务器内容', 'ticket_type':'投诉'}}
        self.snapshots = []

    async def get_state(self, session_id):
        return {'conversation_id':42, 'actions':deepcopy(self.actions)} if session_id == 's' else {}

    async def save_actions(self, session_id, actions):
        self.snapshots.append(deepcopy(actions))
        self.actions = deepcopy(actions)


class Store:
    def get_or_create_conversation(self, session_id):
        return 42


def request(**kwargs):
    return ConfirmTicketRequest(session_id='s', action_id='a', confirmed=True, **kwargs)


def test_unconfirmed_or_extra_conversation_id_rejected():
    with pytest.raises(ValidationError):
        ConfirmTicketRequest(session_id='s', action_id='a', confirmed=False)
    with pytest.raises(ValidationError):
        request(conversation_id=666)


async def test_wrong_session_or_kind_rejected():
    runtime = Runtime()
    with pytest.raises(ActionError):
        await confirm_ticket(ConfirmTicketRequest(session_id='wrong', action_id='a', confirmed=True), runtime=runtime, store=Store())
    runtime.actions['a']['kind'] = 'handoff'
    with pytest.raises(ActionError):
        await confirm_ticket(request(), runtime=runtime, store=Store())


async def test_description_and_conversation_are_server_owned():
    runtime = Runtime()
    called = []
    def create(description, ticket_type, *, conversation_id):
        called.append((description, ticket_type, conversation_id))
        assert runtime.actions['a']['status'] == 'creating'
        return '工单创建成功，工单号 T202610220001'
    result = await confirm_ticket(request(), runtime=runtime, store=Store(), create_once=create)
    assert called == [('服务器内容', '投诉', 42)]
    assert result.status == 'completed'
    assert runtime.snapshots[0]['a']['status'] == 'creating'


async def test_completed_action_returns_original_result():
    runtime = Runtime()
    calls = []
    def create(*args, **kwargs):
        calls.append(1)
        return '工单 T001'
    first = await confirm_ticket(request(), runtime=runtime, store=Store(), create_once=create)
    second = await confirm_ticket(request(), runtime=runtime, store=Store(), create_once=create)
    assert first.result == second.result == '工单 T001'
    assert calls == [1]


async def test_two_concurrent_confirmations_create_once():
    runtime, calls = Runtime(), []
    def create(*args, **kwargs):
        calls.append(1)
        return '工单 T001'
    results = await asyncio.gather(*(confirm_ticket(request(), runtime=runtime, store=Store(), create_once=create) for _ in range(2)))
    assert calls == [1]
    assert all(r.status == 'completed' for r in results)


async def test_ticket_success_then_checkpoint_failure_is_uncertain():
    class Broken(Runtime):
        async def save_actions(self, session_id, actions):
            if actions['a']['status'] == 'completed':
                raise RuntimeError('checkpoint failed')
            await super().save_actions(session_id, actions)
    runtime, calls = Broken(), []
    def create(*args, **kwargs):
        calls.append(1)
        return '工单 T001'
    with pytest.raises(ActionError):
        await confirm_ticket(request(), runtime=runtime, store=Store(), create_once=create)
    with pytest.raises(ActionError):
        await confirm_ticket(request(), runtime=runtime, store=Store(), create_once=create)
    assert calls == [1]
    assert runtime.actions['a']['status'] in {'creating', 'uncertain'}


async def test_chat_and_action_share_lock():
    runtime = Runtime()
    entered = asyncio.Event()
    def create(*args, **kwargs):
        return '工单 T001'
    async with runtime.locks.hold('s'):
        pending = asyncio.create_task(confirm_ticket(request(), runtime=runtime, store=Store(), create_once=create))
        await asyncio.sleep(0)
        assert not pending.done()
    assert (await pending).status == 'completed'


async def test_reopen_creating_does_not_reexecute(tmp_path):
    path = str(tmp_path / 'actions.sqlite')
    async with open_checkpointer(path) as saver:
        runtime = await make_runtime(saver)
        _ = [e async for e in runtime.stream_turn('s', '投诉', model=Gateway('投诉'))]
        state = await runtime.get_state('s')
        action = next(a for a in state['actions'].values() if a['kind'] == 'create_ticket')
        state['actions'][action['action_id']]['status'] = 'creating'
        await runtime.save_actions('s', state['actions'])
        store = runtime.store
    async with open_checkpointer(path) as saver:
        runtime = await make_runtime(saver, store)
        with pytest.raises(ActionError):
            await confirm_ticket(ConfirmTicketRequest(session_id='s', action_id=action['action_id'], confirmed=True), runtime=runtime, store=store,
                                 create_once=lambda *args, **kwargs: pytest.fail('must not replay'))


async def test_http_confirmation_validates_body_and_returns_result(client, monkeypatch):
    from app.main import app
    import app.api.chat_actions as api
    from app.schemas import TicketActionResult
    async def confirmed(request, **kwargs):
        assert request.confirmed is True
        return TicketActionResult(action_id=request.action_id, status='completed', result='工单 T001')
    monkeypatch.setattr(api, 'confirm_ticket', confirmed)
    monkeypatch.setattr(app.state, 'workflow_runtime', type('Runtime', (), {'store':Store()})())
    response = await client.post('/api/chat/actions/ticket', json={'session_id':'s','action_id':'a','confirmed':True})
    assert response.status_code == 200 and response.json()['result'] == '工单 T001'


async def test_checkpoint_failure_prevents_invoking_ticket_tool():
    class Broken(Runtime):
        async def save_actions(self, *args):
            raise RuntimeError('failure before creating')
    with pytest.raises(ActionError):
        await confirm_ticket(request(), runtime=Broken(), store=Store(),
                             create_once=lambda *args, **kwargs: pytest.fail('must not write'))
