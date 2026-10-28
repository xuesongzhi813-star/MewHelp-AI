"""Regression cases reproduced from the final independent review."""
import json
from dataclasses import replace
import pytest
from app.tooling.contracts import RawToolResult
from app.workflow.ticket_draft import validate_ticket_draft
from tests.test_ticket_draft import row,request
from tests.tooling_fakes import make_engine


@pytest.mark.parametrize('change',['revoke','endpoint','definition'])
async def test_read_retry_rechecks_local_permission_endpoint_and_definition(tmp_path,change):
    engine,_,audit,_,ctx,call,snapshot,path=make_engine(tmp_path,source='vendor')
    first_calls=[];replacement_calls=[]
    async def replacement(args,ctx):
        replacement_calls.append(args)
        return RawToolResult({'status':'new target'})
    async def handler(args,ctx):
        first_calls.append(args)
        if len(first_calls)==1:
            data=json.loads(path.read_text())
            if change=='revoke':data['rules']['vendor/lookup']['enabled']=False
            elif change=='endpoint':data['servers']['vendor']['url']='http://127.0.0.1:8022/mcp'
            else:
                definition=engine.registry.resolve('vendor','lookup')
                engine.registry.register(replace(definition,description='替换后的工具',handler=replacement),replace=True)
            path.write_text(json.dumps(data),encoding='utf-8')
            raise ConnectionError('synthetic network interruption')
        return RawToolResult({'status':'stale target'})
    definition=engine.registry.resolve('vendor','lookup')
    engine.registry.register(replace(definition,handler=handler),replace=True)
    result=await engine.dispatch(call,ctx,snapshot)
    assert result.outcome=='permission_denied'
    assert len(first_calls)==1 and not replacement_calls
    assert len(audit.rows)==1 and audit.rows[0].result.retry_count==0


def test_type_only_reply_completes_collecting_draft_and_preserves_verified_description():
    first=row(1,'帮我建工单，包裹未收到')
    initial=request(ticket_type=None,description_sources=[{'message_id':'db:1','quote':'包裹未收到'}])
    draft=validate_ticket_draft(initial,user_rows=[first],prior_draft=None,current_turn=1)
    assert draft.missing==('ticket_type',)
    complete=validate_ticket_draft({'requested_operation':'none','ticket_type':'售后'},
        user_rows=[first,row(2,'售后')],prior_draft=draft,current_turn=2)
    assert complete.status=='ready' and complete.ticket_type=='售后'
    assert complete.description=='包裹未收到'
    assert complete.description_sources==draft.description_sources


@pytest.mark.parametrize('text,quote',[
    ('如果我要建工单，需要什么资料？','我要建工单'),
    ('假如帮我建工单，会发生什么？','帮我建工单'),
    ('请解释帮我建工单这句话','帮我建工单'),
    ("'帮我建工单'是什么意思？",'帮我建工单'),
])
def test_hypothetical_and_unquoted_mentions_cannot_authorize_even_if_model_claims_create(text,quote):
    assert validate_ticket_draft(request(request_quote=quote),
        user_rows=[row(1,text)],prior_draft=None,current_turn=1) is None


def test_long_negation_blocks_extracted_positive_fragment():
    text='我不希望你现在给我建工单'
    assert validate_ticket_draft(request(request_quote='给我建工单'),
        user_rows=[row(1,text)],prior_draft=None,current_turn=1) is None


def test_none_operation_cannot_repurpose_an_old_request_for_a_new_topic():
    first=row(1,'帮我建工单')
    draft=validate_ticket_draft(request(),user_rows=[first],prior_draft=None,current_turn=1)
    info={'requested_operation':'none','ticket_type':'售后',
        'description_sources':[{'message_id':'db:2','quote':'这件衣服怎么清洗'}]}
    assert validate_ticket_draft(info,user_rows=[first,row(2,'这件衣服怎么清洗')],
        prior_draft=draft,current_turn=2) is None


async def test_mcp_request_is_bound_to_the_admitted_endpoint():
    from types import SimpleNamespace
    from app.tooling.mcp_client import MCPManager
    import time
    manager=MCPManager()
    manager.owners['vendor']=SimpleNamespace(url='http://127.0.0.1:8022/mcp',
        task=SimpleNamespace(done=lambda:False))
    with pytest.raises(ConnectionError):
        await manager.call('vendor','lookup',{'id':'1001'},deadline=time.monotonic()+2,
            expected_url='http://127.0.0.1:8021/mcp')


def test_whole_user_message_reference_preserves_all_2000_characters():
    first=row(1,'帮我建工单')
    draft=validate_ticket_draft(request(),user_rows=[first],prior_draft=None,current_turn=1)
    description=('订单1001未收到包裹。'*200)[:2000]
    info=request(description_sources=[{'message_id':'db:2','whole_message':True}])
    complete=validate_ticket_draft(info,user_rows=[first,row(2,description)],prior_draft=draft,current_turn=2)
    assert complete.status=='ready' and complete.description==description


async def test_legacy_refund_keeps_legal_full_text_over_2000_characters(tmp_path,monkeypatch):
    from tests.test_tool_integration import make_service
    from tests.test_refund_actions import setup
    from tests.test_chat_actions import Store
    from app.workflow.refund_actions import confirm_refund
    from app import tools
    service,_,audit=make_service(tmp_path)
    runtime,body=setup();runtime.tool_service=service
    runtime.actions['a']['description']='原问题：'+'衣'*1513
    body=body.model_copy(update={'note':'补'*500})
    calls=[]
    def create(description,ticket_type,conversation_id):
        calls.append(description)
        return json.dumps({'ticket_no':'TK-LONG-OLD','ticket_type':ticket_type},ensure_ascii=False)
    monkeypatch.setattr(tools.create_ticket,'func',create)
    await service.start()
    try:
        result=await confirm_refund(body,runtime=runtime,store=Store())
        assert result.status=='completed' and len(calls)==1 and len(calls[0])>2000
        assert '补'*500 in calls[0] and '衣'*1513 in calls[0]
        assert len(audit.rows)==1 and audit.rows[0].result.status=='success'
    finally:await service.aclose()


async def test_known_legacy_validation_rejection_is_not_uncertain():
    from types import SimpleNamespace
    from tests.test_chat_actions import Runtime,Store,request as action_request
    from app.workflow.actions import confirm_ticket,ActionError
    from app.tooling.contracts import ToolExecutionResult
    runtime=Runtime()
    async def reject(*args):
        return ToolExecutionResult('参数不合法',status='error',outcome='invalid_arguments')
    runtime.tool_service=SimpleNamespace(confirmed_action=reject)
    with pytest.raises(ActionError) as failure:
        await confirm_ticket(action_request(),runtime=runtime,store=Store())
    assert failure.value.status_code==422
    assert runtime.actions['a']['status']=='rejected'


async def test_draft_reference_executes_exact_verified_2000_character_payload(tmp_path,monkeypatch):
    from tests.test_tool_integration import make_service
    from app.tooling.contracts import ToolCall,ExecutionContext,PreparedCall
    from app import tools
    import time
    service,_,audit=make_service(tmp_path)
    payload={'description':('订单1001未收到包裹。'*200)[:2000],'ticket_type':'售后'}
    reference='b'*32;calls=[]
    def create(description,ticket_type,conversation_id):
        calls.append((description,ticket_type,conversation_id))
        return json.dumps({'ticket_no':'TK-REF-2000','ticket_type':ticket_type})
    monkeypatch.setattr(tools.create_ticket,'func',create)
    snapshot=service.registry.snapshot(service.policy_loader.load())
    call=ToolCall('create_ticket',{'draft_id':reference},'call-ref','ref-key')
    ctx=ExecutionContext('s',42,'request',time.monotonic()+5,
        {'ticket_requested':True,'ticket_draft':payload,'ticket_draft_id':reference})
    prepared=service.engine.prepare(call,ctx,snapshot)
    assert isinstance(prepared,PreparedCall)
    nonce=service.capabilities.issue('s',call.invocation_key,prepared.args_hash,snapshot.policy_hash)
    result=await service.engine.invoke(prepared,ctx,nonce=nonce)
    assert result.status=='success' and calls==[(payload['description'],'售后',42)]
    assert len(audit.rows)==1


async def test_budget_denied_mcp_call_keeps_registered_source(tmp_path):
    from tests.test_tool_integration import make_service
    from app.tooling.scope import tool_scope
    from app.workflow.agent import execute_step,new_budget
    from langchain_core.messages import AIMessage
    service,_,audit=make_service(tmp_path);await service.start()
    try:
        snapshot=await service.refresh()
        budget=new_budget();budget.tool_calls=budget.max_tool_calls
        with tool_scope(service,snapshot,{'session_id':'s','request_id':'r','conversation_id':42}):
            observations,_=await execute_step(AIMessage(content='',tool_calls=[{
                'name':'mcp__vendor__lookup','args':{'id':'1001'},'id':'denied'}]),
                budget=budget,context={},execute=lambda *a:None,emit=lambda e:None)
        assert len(observations)==len(audit.rows)==1
        assert audit.rows[0].ctx.trusted.get('audit_source')=='mcp'
        assert audit.rows[0].ctx.trusted.get('audit_server')=='vendor'
    finally:await service.aclose()


@pytest.mark.parametrize('trusted',[
    {},{'ticket_requested':True},
    {'ticket_requested':True,'ticket_draft':{'description':'包裹未收到','ticket_type':'售后'},'ticket_draft_id':'c'*32},
])
async def test_unverified_or_foreign_draft_reference_cannot_execute(tmp_path,trusted):
    from tests.test_tool_integration import make_service
    from app.tooling.contracts import ToolCall,ExecutionContext,RejectedCall
    import time
    service,_,_=make_service(tmp_path)
    snapshot=service.registry.snapshot(service.policy_loader.load())
    call=ToolCall('create_ticket',{'draft_id':'b'*32},'forged-ref','forged-key')
    ctx=ExecutionContext('s',42,'request',time.monotonic()+5,trusted)
    assert isinstance(service.engine.prepare(call,ctx,snapshot),RejectedCall)


async def test_reference_capability_binds_full_resolved_text_not_only_short_id(tmp_path,monkeypatch):
    from tests.test_tool_integration import make_service
    from app.tooling.contracts import ToolCall,ExecutionContext
    from app import tools
    import time
    service,_,audit=make_service(tmp_path);calls=[]
    monkeypatch.setattr(tools.create_ticket,'func',lambda **args:calls.append(args))
    payload={'description':'包裹未收到','ticket_type':'售后'};reference='b'*32
    snapshot=service.registry.snapshot(service.policy_loader.load())
    call=ToolCall('create_ticket',{'draft_id':reference},'bound-ref','bound-key')
    ctx=ExecutionContext('s',42,'request',time.monotonic()+5,
        {'ticket_requested':True,'ticket_draft':payload,'ticket_draft_id':reference})
    prepared=service.engine.prepare(call,ctx,snapshot)
    nonce=service.capabilities.issue('s',call.invocation_key,prepared.args_hash,snapshot.policy_hash)
    changed=replace(ctx,trusted={**ctx.trusted,'ticket_draft':{**payload,'description':'另一个问题'}})
    result=await service.engine.invoke(prepared,changed,nonce=nonce)
    assert result.outcome=='permission_denied' and not calls


def test_whole_message_reference_does_not_accept_assistant_or_extra_quote():
    first=row(1,'帮我建工单')
    draft=validate_ticket_draft(request(),user_rows=[first],prior_draft=None,current_turn=1)
    for source in [row(2,'包裹未收到',role='assistant'),row(2,'包裹未收到',conversation_id=99)]:
        info=request(description_sources=[{'message_id':'db:2','whole_message':True}])
        assert validate_ticket_draft(info,user_rows=[first,source,row(3,'补充描述')],prior_draft=draft,current_turn=3) is None
    info=request(description_sources=[{'message_id':'db:2','whole_message':True,'quote':'改写的问题'}])
    assert validate_ticket_draft(info,user_rows=[first,row(2,'包裹未收到')],prior_draft=draft,current_turn=2) is None


async def test_failed_question_rewrite_preserves_independently_verifiable_ticket_sources():
    from app.workflow.understanding import understand_question
    from app.workflow.contracts import BudgetState
    from tests.test_workflow_understanding import Gateway
    from langchain_core.messages import HumanMessage
    first=row(1,'帮我建工单');text='订单1001未收到包裹。'*30
    draft=validate_ticket_draft(request(),user_rows=[first],prior_draft=None,current_turn=1)
    info=request(standalone_question='订单1001未收到包裹',reference_status='resolved',
        order_id='1001',source_message_id=None,
        description_sources=[{'message_id':'db:2','whole_message':True}])
    understood=await understand_question(text,history=[HumanMessage('帮我建工单',id='db:1')],
        gateway=Gateway(info),budget=BudgetState(),current_message_id='db:2')
    assert understood.standalone_question==text and understood.reference_status=='ambiguous'
    assert understood.order_id is None
    complete=validate_ticket_draft(understood,user_rows=[first,row(2,text)],prior_draft=draft,current_turn=2)
    assert complete.status=='ready' and complete.description==text


def test_explicit_continuation_with_missing_selector_uses_current_actual_user_reply():
    first=row(1,'帮我建工单')
    draft=validate_ticket_draft(request(),user_rows=[first],prior_draft=None,current_turn=1)
    text=('包裹未收到，日志包含字符和双引号。'*150)[:2000]
    complete=validate_ticket_draft(request(),user_rows=[first,row(2,text)],prior_draft=draft,current_turn=2)
    assert complete.status=='ready' and complete.description==text
    assert complete.description_sources==({'message_id':'db:2','quote':text},)


@pytest.mark.parametrize('text',['谢谢','收到','好的','帮我建工单'])
def test_slot_collection_does_not_turn_acknowledgments_into_problem_text(text):
    first=row(1,'帮我建工单')
    draft=validate_ticket_draft(request(),user_rows=[first],prior_draft=None,current_turn=1)
    result=validate_ticket_draft(request(),user_rows=[first,row(2,text)],prior_draft=draft,current_turn=2)
    assert result is None or result.status!='ready'
