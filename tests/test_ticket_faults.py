import json
from dataclasses import asdict
import pytest
from app.schemas import ToolConfirmationRequest
from app.workflow.runtime import WorkflowRuntime,open_checkpointer
from app.workflow.tool_calls import prepare_ticket_write
from tests.test_ticket_interrupt import setup_runtime


async def test_waiting_interrupt_survives_real_sqlite_reopen(tmp_path):
    path=str(tmp_path/'reopen.sqlite')
    async with open_checkpointer(path) as saver:
        runtime,service,audit,calls,gateway=await setup_runtime(tmp_path,saver)
        _=[e async for e in runtime.stream_turn('s','包裹未收到，帮我建工单',model=gateway)]
        store=runtime.store
        preview=(await runtime.get_state('s'))['ticket_confirmation']['preview_id']
    async with open_checkpointer(path) as saver:
        runtime=WorkflowRuntime(saver,store,gateway_factory=lambda _:gateway,tool_service=service)
        assert (await runtime.public_session_state('s'))['ticket_confirmation']['preview_id']==preview
        _=[e async for e in runtime.stream_tool_confirmation(ToolConfirmationRequest(
            session_id='s',preview_id=preview,decision='confirm'),model=gateway)]
        assert len(calls)==1
    await service.aclose()


async def test_new_user_turn_expires_pending_and_pairs_old_call(tmp_path):
    async with open_checkpointer(str(tmp_path/'expire.sqlite')) as saver:
        runtime,service,audit,calls,gateway=await setup_runtime(tmp_path,saver)
        try:
            _=[e async for e in runtime.stream_turn('s','包裹未收到，帮我建工单',model=gateway)]
            # A new unrelated turn must not inherit authorization or consume resume.
            gateway.intent='闲聊'
            async def understand(messages,budget):
                return {'standalone_question':'谢谢','reference_status':'unchanged'}
            gateway.understand=understand
            _=[e async for e in runtime.stream_turn('s','谢谢',model=gateway)]
            state=await runtime.get_state('s')
            assert not calls and len(audit.rows)==1 and audit.rows[0].result.outcome=='permission_denied'
            assert state['ticket_draft'] is None
            assert any(m.type=='tool' and m.tool_call_id=='ticket-call' for m in state['messages'])
        finally:
            await service.aclose()


async def test_after_business_commit_checkpoint_failure_is_uncertain_after_reopen(tmp_path):
    from app.workflow.tool_calls import execute_ticket_write
    path=str(tmp_path/'committed.sqlite')
    async with open_checkpointer(path) as saver:
        runtime,service,audit,calls,gateway=await setup_runtime(tmp_path,saver)
        _=[e async for e in runtime.stream_turn('s','包裹未收到，帮我建工单',model=gateway)]
        state=await runtime.get_state('s');state['ticket_confirmation']['status']='confirmed'
        state.update(prepare_ticket_write(state,service))
        # Persist creating; deliberately lose the returned result checkpoint.
        await runtime.graph().aupdate_state(runtime.config('s'),
            {'ticket_confirmation':state['ticket_confirmation']},as_node='prepare_ticket_write')
        result=await execute_ticket_write(state,service)
        assert result['ticket_confirmation']['status']=='completed' and len(calls)==1
        store=runtime.store
        service.capabilities.clear()
    async with open_checkpointer(path) as saver:
        runtime=WorkflowRuntime(saver,store,gateway_factory=lambda _:gateway,tool_service=service)
        state=await runtime.get_state('s')
        result=await execute_ticket_write(state,service)
        assert result['ticket_confirmation']['status']=='uncertain' and len(calls)==1
        assert (await runtime.public_session_state('s'))['ticket_confirmation']['status']=='uncertain'
    await service.aclose()


async def test_write_preparation_checkpoint_failure_does_not_call_handler(tmp_path,monkeypatch):
    import app.workflow.tool_calls as module
    async with open_checkpointer(str(tmp_path/'prepare.sqlite')) as saver:
        runtime,service,audit,calls,gateway=await setup_runtime(tmp_path,saver)
        try:
            _=[e async for e in runtime.stream_turn('s','包裹未收到，帮我建工单',model=gateway)]
            def fail(state,service):
                raise RuntimeError('synthetic preparation checkpoint outage')
            monkeypatch.setattr(module,'prepare_ticket_write',fail)
            preview=(await runtime.get_state('s'))['ticket_confirmation']['preview_id']
            events=[e async for e in runtime.stream_tool_confirmation(ToolConfirmationRequest(
                session_id='s',preview_id=preview,decision='confirm'),model=gateway)]
            assert not calls and any(e['type']=='error' for e in events)
        finally:
            await service.aclose()


@pytest.mark.parametrize('failed_status,expected_calls',[('confirmed',0),('creating',0),('completed',1)])
async def test_real_checkpoint_write_failure_cannot_start_or_repeat_business_write(tmp_path,monkeypatch,failed_status,expected_calls):
    async with open_checkpointer(str(tmp_path/(failed_status+'.sqlite'))) as saver:
        runtime,service,audit,calls,gateway=await setup_runtime(tmp_path,saver)
        try:
            _=[e async for e in runtime.stream_turn('s','包裹未收到，帮我建工单',model=gateway)]
            original=saver.aput;failed=False
            async def fail_once(config,checkpoint,metadata,new_versions):
                nonlocal failed
                confirmation=checkpoint.get('channel_values',{}).get('ticket_confirmation') or {}
                if not failed and confirmation.get('status')==failed_status:
                    failed=True
                    raise OSError('synthetic SQLite write failure')
                return await original(config,checkpoint,metadata,new_versions)
            monkeypatch.setattr(saver,'aput',fail_once)
            preview=(await runtime.get_state('s'))['ticket_confirmation']['preview_id']
            events=[e async for e in runtime.stream_tool_confirmation(ToolConfirmationRequest(
                session_id='s',preview_id=preview,decision='confirm'),model=gateway)]
            assert failed and len(calls)==expected_calls
            assert any(e['type']=='error' for e in events)
            if expected_calls:
                assert (await runtime.public_session_state('s'))['ticket_confirmation']['status']=='uncertain'
        finally:
            await service.aclose()
