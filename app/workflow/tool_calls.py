"""Checkpointed ticket admission and one-shot write execution."""
from dataclasses import asdict
import time
import uuid
from langchain_core.messages import AIMessage,ToolMessage
from app.tooling.contracts import PreparedCall,ToolCall,ToolExecutionResult,RejectedCall


def trusted_ticket(state):
    draft=state.get('ticket_draft')
    if not draft or draft['status']!='ready' or state.get('ticket_flow_closed'):
        return {}
    from app.workflow.ticket_draft import draft_reference
    return {'ticket_requested':True,'ticket_draft_id':draft_reference(draft),'ticket_draft':{
        'description':draft['description'],'ticket_type':draft['ticket_type']}}


def execution_input(state,service):
    confirmation=state['ticket_confirmation']
    call=ToolCall(**confirmation['call'])
    _,ctx,snapshot=service.execution_input(call.name,call.args,
        context={'session_id':state['session_id'],'conversation_id':state['conversation_id'],
            'request_id':confirmation['request_id'],'deadline':state['budget']['deadline'],
            'trusted':{'ticket_requested':True,'ticket_draft':confirmation['args'],
                'ticket_draft_id':confirmation.get('draft_id')}},
        tool_call_id=call.tool_call_id,snapshot=state['tool_snapshot'])
    return call,ctx,snapshot


def prepare_tool_call(state,service):
    """No handler, audit, nonce or random identity before this checkpoint."""
    if service is None or not state.get('pending_decision'):
        return {}
    response=AIMessage.model_validate(state['pending_decision'])
    if len(response.tool_calls)!=1 or response.tool_calls[0]['name']!='create_ticket':
        return {}
    item=response.tool_calls[0]
    call,ctx,snapshot=service.execution_input(item['name'],item['args'],
        context={'session_id':state['session_id'],'conversation_id':state['conversation_id'],
            'request_id':state['request_id'],'deadline':state['budget']['deadline'],
            'trusted':{**trusted_ticket(state),'budget_exhausted':
                state['budget']['tool_calls']>=state['budget']['max_tool_calls']}},
        tool_call_id=item['id'],snapshot=state['tool_snapshot'])
    prepared=service.engine.prepare(call,ctx,snapshot)
    if isinstance(prepared,RejectedCall):
        return {}
    return {'ticket_confirmation':{'preview_id':uuid.uuid5(uuid.NAMESPACE_URL,'preview:'+call.invocation_key).hex,
        'status':'available','session_id':state['session_id'],'conversation_id':state['conversation_id'],
        'request_id':state['request_id'],'call':asdict(call),'prepared':asdict(prepared),
        'policy_hash':prepared.policy_hash,'args':dict(prepared.resolved_args or call.args),
        'draft_id':ctx.trusted.get('ticket_draft_id'),'expires_at':time.time()+600},
        'status':'awaiting_tool_confirmation'}


def public_preview(confirmation):
    return {'type':'tool_confirmation','preview_id':confirmation['preview_id'],
        'status':confirmation['status'],'ticket_type':confirmation['args']['ticket_type'],
        'description':confirmation['args']['description'],'is_demo':True}


def prepare_ticket_write(state,service):
    """Only confirmed -> creating may issue a capability; execute never issues."""
    confirmation=state['ticket_confirmation']
    if confirmation['status']!='confirmed':
        raise ValueError('ticket not confirmed')
    call,ctx,snapshot=execution_input(state,service)
    prepared=service.engine.prepare(call,ctx,snapshot)
    nonce=None
    if not isinstance(prepared,RejectedCall) and prepared.policy_hash==confirmation['policy_hash']:
        nonce=service.capabilities.issue(ctx.session_id,call.invocation_key,prepared.args_hash,prepared.policy_hash)
    return {'ticket_confirmation':{**confirmation,'status':'creating','nonce':nonce},'status':'running'}


async def reject_ticket(state,service,*,status='cancelled'):
    confirmation=state['ticket_confirmation']
    call,ctx,_=execution_input(state,service)
    result=ToolExecutionResult('权限拒绝：客户已取消或预览已失效，未提交工单。',
        status='error',outcome='permission_denied',error_code=status)
    await service.engine.finish(call,ctx,result)
    return {'ticket_confirmation':{**confirmation,'status':status},'ticket_draft':None,
        'ticket_flow_closed':True,'status':'running','messages':[
            ToolMessage(content=result.content,tool_call_id=call.tool_call_id)]}


async def execute_ticket_write(state,service):
    confirmation=state['ticket_confirmation']
    if confirmation['status']!='creating':
        raise ValueError('ticket not creating')
    call,ctx,snapshot=execution_input(state,service)
    prepared_data={**confirmation['prepared'],'call':call}
    prepared=PreparedCall(**prepared_data)
    if service.policy_loader.load().version!=confirmation['policy_hash']:
        result=await service.engine.finish(call,ctx,ToolExecutionResult('权限拒绝：确认期间工具策略已变化，请重新核对。',
            status='error',outcome='permission_denied',error_code='policy_changed'))
    else:
        result=await service.engine.invoke(prepared,ctx,nonce=confirmation.get('nonce'))
    if result.status=='success':
        status='completed'
    elif result.error_code=='confirmation_required' or result.outcome in {'execution_error','timeout'}:
        status='uncertain'
    else:
        status='rejected'
    content=result.content if status!='uncertain' else '工单提交结果待核实，请联系人工客服核实，请勿重复提交。'
    budget=dict(state['budget']); budget['tool_calls']+=1
    return {'ticket_confirmation':{**confirmation,'nonce':None,'status':status,'result':content},
        'ticket_draft':None,'ticket_flow_closed':True,'budget':budget,
        'messages':[ToolMessage(content=content,tool_call_id=call.tool_call_id)],'status':'running'}
