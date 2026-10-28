import asyncio
import httpx
import pytest
from fastapi import FastAPI
from app.api.chat_actions import router
from app.dependencies import get_chat_model
from app.schemas import ToolConfirmationRequest
from app.workflow.actions import ActionError
from app.workflow.runtime import open_checkpointer
from tests.test_ticket_interrupt import setup_runtime


async def test_confirmation_api_strict_payload_cross_session_and_repeat(tmp_path):
    async with open_checkpointer(str(tmp_path/'api.sqlite')) as saver:
        runtime,service,audit,calls,gateway=await setup_runtime(tmp_path,saver)
        app=FastAPI(); app.include_router(router); app.state.workflow_runtime=runtime
        app.dependency_overrides[get_chat_model]=lambda:gateway
        try:
            _=[e async for e in runtime.stream_turn('s','包裹未收到，帮我建工单',model=gateway)]
            preview=(await runtime.get_state('s'))['ticket_confirmation']['preview_id']
            body={'session_id':'s','preview_id':preview,'decision':'confirm'}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                invalid=await client.post('/api/chat/actions/tool-confirmation',json={**body,'description':'forged'})
                assert invalid.status_code==422 and not calls
                other=await client.post('/api/chat/actions/tool-confirmation',json={**body,'session_id':'other'})
                assert other.status_code==404 and not calls
                wrong=await client.post('/api/chat/actions/tool-confirmation',json={**body,'preview_id':'wrong'})
                assert wrong.status_code==409 and not calls
                success=await client.post('/api/chat/actions/tool-confirmation',json=body)
                assert success.status_code==200 and 'tool_confirmation_result' in success.text
                assert 'TK-DEMO-001' in success.text and 'done' in success.text
                repeat=await client.post('/api/chat/actions/tool-confirmation',json=body)
                assert repeat.status_code==409 and len(calls)==1
        finally:
            await service.aclose()


async def test_preflight_does_not_authorize_racing_second_resume(tmp_path):
    async with open_checkpointer(str(tmp_path/'race.sqlite')) as saver:
        runtime,service,audit,calls,gateway=await setup_runtime(tmp_path,saver)
        try:
            _=[e async for e in runtime.stream_turn('s','包裹未收到，帮我建工单',model=gateway)]
            preview=(await runtime.get_state('s'))['ticket_confirmation']['preview_id']
            request=ToolConfirmationRequest(session_id='s',preview_id=preview,decision='confirm')
            await runtime.validate_tool_confirmation(request)
            async def submit():
                return [e async for e in runtime.stream_tool_confirmation(request,model=gateway)]
            results=await asyncio.gather(submit(),submit())
            assert len(calls)==1
            assert sum(any(e['type']=='error' for e in events) for events in results)==1
        finally:
            await service.aclose()


async def test_revocation_while_waiting_never_writes(tmp_path):
    async with open_checkpointer(str(tmp_path/'revoke.sqlite')) as saver:
        runtime,service,audit,calls,gateway=await setup_runtime(tmp_path,saver)
        try:
            _=[e async for e in runtime.stream_turn('s','包裹未收到，帮我建工单',model=gateway)]
            import json
            policy=json.loads(service.policy_loader.path.read_text())
            policy['rules']['builtin/create_ticket']={'enabled':False,'effect':'write'}
            service.policy_loader.path.write_text(json.dumps(policy),encoding='utf-8')
            preview=(await runtime.get_state('s'))['ticket_confirmation']['preview_id']
            _=[e async for e in runtime.stream_tool_confirmation(ToolConfirmationRequest(
                session_id='s',preview_id=preview,decision='confirm'),model=gateway)]
            assert not calls and audit.rows[-1].result.outcome=='permission_denied'
        finally:
            await service.aclose()
