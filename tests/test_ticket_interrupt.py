import json
from dataclasses import asdict
from langgraph.types import Command
from app.history import InMemorySessionStore
from app.workflow.runtime import WorkflowRuntime,open_checkpointer
from tests.test_tool_integration import make_service
from tests.test_workflow_graph import Gateway
from app.tooling.contracts import RawToolResult
from dataclasses import replace


class TicketGateway(Gateway):
    def __init__(self):
        super().__init__(intent='售后',rounds=[
            [{'name':'create_ticket','args':{'description':'包裹未收到','ticket_type':'售后'},'id':'ticket-call'}],
            [{'name':'respond_to_user','args':{'response_mode':'answer','actions':[]},'id':'respond'}]])

    async def understand(self,messages,budget):
        payload=json.loads(messages[-1].content)
        return {'standalone_question':payload['question'],'reference_status':'unchanged',
            'requested_operation':'create_ticket','request_source_id':payload['question_id'],
            'request_quote':'帮我建工单','ticket_type':'售后',
            'description_sources':[{'message_id':payload['question_id'],'quote':'包裹未收到'}]}


async def setup_runtime(tmp_path,saver):
    service,_,audit=make_service(tmp_path)
    await service.start()
    calls=[]
    definition=service.registry.resolve('builtin','create_ticket')
    async def handler(args,ctx):
        calls.append(args)
        return RawToolResult({'ticket_no':'TK-DEMO-001','ticket_type':'售后','status':'已创建'})
    service.registry.register(replace(definition,handler=handler),replace=True)
    gateway=TicketGateway()
    runtime=WorkflowRuntime(saver,InMemorySessionStore(10),gateway_factory=lambda _:gateway,tool_service=service)
    return runtime,service,audit,calls,gateway


async def test_real_sqlite_interrupt_has_no_write_or_audit_until_confirmation(tmp_path):
    async with open_checkpointer(str(tmp_path/'ticket.sqlite')) as saver:
        runtime,service,audit,calls,gateway=await setup_runtime(tmp_path,saver)
        try:
            events=[e async for e in runtime.stream_turn('s','包裹未收到，帮我建工单',model=gateway)]
            assert events[-2]['type']=='tool_confirmation'
            assert not calls and not audit.rows
            state=await runtime.get_state('s')
            preview=state['ticket_confirmation']
            assert preview['status']=='available' and state['final_text']==''
            assert (await runtime.public_session_state('s'))['ticket_confirmation']['preview_id']==preview['preview_id']
            from app.schemas import ToolConfirmationRequest
            request=ToolConfirmationRequest(session_id='s',preview_id=preview['preview_id'],decision='confirm')
            events=[e async for e in runtime.stream_tool_confirmation(request,model=gateway)]
            assert events[-1]['type']=='done'
            assert len(calls)==len(audit.rows)==1
            final=await runtime.get_state('s')
            assert final['ticket_confirmation']['status']=='completed'
            tool=next(m for m in final['messages'] if m.type=='tool' and m.tool_call_id=='ticket-call')
            assert 'TK-DEMO-001' in tool.content
        finally:
            await service.aclose()


async def test_cancel_pairs_result_audits_permission_and_never_writes(tmp_path):
    async with open_checkpointer(str(tmp_path/'cancel.sqlite')) as saver:
        runtime,service,audit,calls,gateway=await setup_runtime(tmp_path,saver)
        try:
            _=[e async for e in runtime.stream_turn('s','包裹未收到，帮我建工单',model=gateway)]
            from app.schemas import ToolConfirmationRequest
            preview=(await runtime.get_state('s'))['ticket_confirmation']
            events=[e async for e in runtime.stream_tool_confirmation(ToolConfirmationRequest(
                session_id='s',preview_id=preview['preview_id'],decision='cancel'),model=gateway)]
            assert not calls and len(audit.rows)==1
            assert audit.rows[0].result.outcome=='permission_denied'
            assert (await runtime.get_state('s'))['ticket_confirmation']['status']=='cancelled'
            assert events[-1]['type']=='done'
        finally:
            await service.aclose()
