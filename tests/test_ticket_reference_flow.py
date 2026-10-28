"""Long descriptions survive a native checkpoint without model regeneration."""
import json
import re
import pytest
from langchain_core.messages import AIMessage
from app.schemas import ToolConfirmationRequest
from app.workflow.runtime import WorkflowRuntime,open_checkpointer
from tests.test_ticket_interrupt import setup_runtime,TicketGateway


class ReferenceGateway(TicketGateway):
    def __init__(self):
        super().__init__();self.request_source=None;self.sent=False

    async def understand(self,messages,budget):
        data=json.loads(messages[-1].content)
        if self.request_source is None:self.request_source=data['question_id']
        return {'standalone_question':data['question'],'reference_status':'unchanged',
            'requested_operation':'create_ticket','request_source_id':self.request_source,
            'request_quote':'帮我建工单','ticket_type':'售后','description_sources':[]
            if data['question']=='帮我建工单' else [{'message_id':data['question_id'],'whole_message':True}]}

    async def decide(self,messages,budget):
        matches=re.findall(r'"draft_id":\s*"([a-f0-9]{32})"','\n'.join(str(m.content) for m in messages))
        if matches and not self.sent:
            self.sent=True
            return AIMessage(content='',tool_calls=[{'name':'create_ticket','args':{'draft_id':matches[-1]},'id':'ref'}])
        return AIMessage(content='',tool_calls=[{'name':'respond_to_user','args':{
            'response_mode':'answer' if self.sent else 'clarify','actions':[]},'id':'respond'}])


@pytest.mark.parametrize('decision',['confirm','cancel'])
async def test_full_reference_preview_survives_sqlite_reopen(tmp_path,decision):
    path=str(tmp_path/'ref.sqlite');description=('订单1001未收到包裹。'*200)[:2000]
    async with open_checkpointer(path) as saver:
        runtime,service,audit,calls,_=await setup_runtime(tmp_path,saver)
        gateway=ReferenceGateway();runtime.gateway_factory=lambda _:gateway
        first=[e async for e in runtime.stream_turn('s','帮我建工单',model=gateway)]
        assert not calls and not any(e['type']=='tool_confirmation' for e in first)
        second=[e async for e in runtime.stream_turn('s',description,model=gateway)]
        preview=next(e for e in second if e['type']=='tool_confirmation')
        assert preview['description']==description and len(description)==2000
        state=await runtime.get_state('s');store=runtime.store
        assert set(state['ticket_confirmation']['call']['args'])=={'draft_id'}
    service.capabilities.clear()
    try:
        async with open_checkpointer(path) as saver:
            runtime=WorkflowRuntime(saver,store,gateway_factory=lambda _:gateway,tool_service=service)
            frames=[e async for e in runtime.stream_tool_confirmation(ToolConfirmationRequest(
                session_id='s',preview_id=preview['preview_id'],decision=decision),model=gateway)]
            assert frames[-1]['type']=='done'
            if decision=='confirm':
                assert len(calls)==1 and calls[0]['description']==description
                assert audit.rows[0].ctx.trusted['audit_arguments']['description']==description
            else:assert not calls and audit.rows[0].result.outcome=='permission_denied'
    finally:await service.aclose()
