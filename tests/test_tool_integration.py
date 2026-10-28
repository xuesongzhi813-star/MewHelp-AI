"""The production workflow must consume registered tools through one engine."""
import asyncio
import json
import time
from dataclasses import asdict
import pytest
from langchain_core.messages import AIMessage
from app.history import InMemorySessionStore
from app.tooling.contracts import ToolDefinition,RawToolResult,ToolCall,ExecutionContext
from app.tooling.policy import PolicyLoader
from app.tooling.service import ToolService
from app.workflow.runtime import WorkflowRuntime,open_checkpointer
from tests.tooling_fakes import FakeAuditSink
from tests.test_workflow_graph import Gateway


class Manager:
    def __init__(self):
        self.calls=[]
    async def start(self): pass
    async def aclose(self): pass
    async def discover(self,policy):
        if not policy.valid: return {}
        return {"vendor":[ToolDefinition("lookup","查演示订单",{"type":"object","properties":{"id":{"type":"string"}},"required":["id"],"additionalProperties":False},"vendor","lookup")]}
    async def call(self,source,name,args,*,deadline,expected_url=None):
        self.calls.append((source,name,args))
        return RawToolResult({"id":args["id"],"status":"运输中"})


def make_service(tmp_path):
    path=tmp_path/"policy.json"
    path.write_text(json.dumps({"servers":{"vendor":{"url":"http://127.0.0.1:8021/mcp","enabled":True}},
        "rules":{"vendor/lookup":{"enabled":True,"effect":"read","output_fields":["id","status"]}}}),encoding="utf-8")
    manager=Manager()
    audit=FakeAuditSink()
    return ToolService(PolicyLoader(path),manager=manager,audit_sink=audit),manager,audit


async def test_runtime_executes_new_tool_without_core_name_whitelist(tmp_path):
    service,manager,audit=make_service(tmp_path)
    await service.start()
    try:
        gateway=Gateway(rounds=[[{"name":"mcp__vendor__lookup","args":{"id":"1001"},"id":"tool-dynamic"}],
                                [{"name":"respond_to_user","args":{"response_mode":"answer","actions":[]},"id":"respond"}]])
        async with open_checkpointer(str(tmp_path/"runtime.sqlite")) as saver:
            runtime=WorkflowRuntime(saver,InMemorySessionStore(10),gateway_factory=lambda _:gateway,tool_service=service)
            events=[e async for e in runtime.stream_turn("s","查订单1001",model=gateway)]
            assert events[-1]["type"]=="done"
            state=await runtime.get_state("s")
            assert state["tool_snapshot"]["version"]
            observations=[m for m in state["messages"] if m.type=="tool" and m.tool_call_id=="tool-dynamic"]
            assert len(observations)==1 and "运输中" in observations[0].content
        assert len(manager.calls)==1
        assert len(audit.rows)==1 and audit.rows[0].call.tool_call_id=="tool-dynamic"
    finally:
        await service.aclose()


async def test_preloaded_faq_goes_through_engine_and_keeps_evidence_artifact(tmp_path):
    from tests.test_workflow_knowledge import result
    service,_,audit=make_service(tmp_path)
    await service.start()
    seen=[]
    def retrieve(question,**kwargs):
        seen.append(question)
        return result(.8)
    try:
        gateway=Gateway(intent='商品咨询')
        async with open_checkpointer(str(tmp_path/'faq.sqlite')) as saver:
            runtime=WorkflowRuntime(saver,InMemorySessionStore(10),gateway_factory=lambda _:gateway,tool_service=service,retrieve=retrieve)
            events=[e async for e in runtime.stream_turn('s','退货政策',model=gateway)]
            state=await runtime.get_state('s')
        assert events[-1]['type']=='done' and len(seen)==1
        assert len(audit.rows)==1 and audit.rows[0].call.name=='query_faq'
        assert audit.rows[0].result.artifact.hits[0].answer=='规则'
        assert state['retrieval']['hits'][0]['answer']=='规则'
    finally:
        await service.aclose()


async def test_disabled_preloaded_faq_never_calls_retriever(tmp_path):
    service,_,audit=make_service(tmp_path)
    path=service.policy_loader.path
    policy=json.loads(path.read_text())
    policy['rules']['builtin/query_faq']={'enabled':False,'effect':'read'}
    path.write_text(json.dumps(policy))
    await service.start()
    seen=[]
    def retrieve(*args,**kwargs):
        seen.append(True)
        raise AssertionError('must not run')
    try:
        gateway=Gateway(intent='商品咨询')
        async with open_checkpointer(str(tmp_path/'disabled.sqlite')) as saver:
            runtime=WorkflowRuntime(saver,InMemorySessionStore(10),gateway_factory=lambda _:gateway,tool_service=service,retrieve=retrieve)
            _=[e async for e in runtime.stream_turn('s','退货政策',model=gateway)]
            state=await runtime.get_state('s')
        assert seen==[]
        assert audit.rows[0].result.outcome=='permission_denied'
        assert any(m.type=='tool' and '权限' in m.content for m in state['messages'])
    finally:
        await service.aclose()


async def test_disabled_preloaded_order_stops_policy_and_audits_denial(tmp_path):
    service,_,audit=make_service(tmp_path)
    path=service.policy_loader.path
    policy=json.loads(path.read_text())
    policy['rules']['builtin/query_order']={'enabled':False,'effect':'read'}
    path.write_text(json.dumps(policy))
    await service.start()
    seen=[]
    def retrieve(*args,**kwargs):
        seen.append(True)
        raise AssertionError('policy must not run without order')
    try:
        gateway=Gateway(intent='退款退货')
        async with open_checkpointer(str(tmp_path/'order-denied.sqlite')) as saver:
            runtime=WorkflowRuntime(saver,InMemorySessionStore(10),gateway_factory=lambda _:gateway,tool_service=service,retrieve=retrieve)
            _=[e async for e in runtime.stream_turn('s','订单1001能退吗',model=gateway)]
            state=await runtime.get_state('s')
        assert seen==[]
        assert audit.rows[0].call.name=='query_order' and audit.rows[0].result.outcome=='permission_denied'
        assert any(m.type=='tool' and '权限' in m.content for m in state['messages'])
    finally:
        await service.aclose()


async def test_legacy_chat_uses_same_dynamic_catalog_and_execution_engine(tmp_path,monkeypatch):
    from tests.test_chat_service import FakeChatModel,FakeStore,parse_sse
    from langchain_core.messages import AIMessageChunk
    from app.chat_service import stream_chat
    from app.config import get_settings
    settings=get_settings().model_copy(update={'chat_workflow_enabled':False,'kb_corpus_version':'ch03'})
    monkeypatch.setattr('app.chat_service.get_settings',lambda:settings)
    service,manager,audit=make_service(tmp_path)
    await service.start()
    model=FakeChatModel([[AIMessageChunk(content='',tool_call_chunks=[{'name':'mcp__vendor__lookup','args':'{"id":"1001"}','id':'legacy-call','index':0}])],[AIMessageChunk(content='查询完成')]])
    try:
        events=parse_sse([frame async for frame in stream_chat('s','查订单1001',store=FakeStore(),model=model,tool_service=service)])
        assert events[-1]['type']=='done' and len(manager.calls)==1
        assert audit.rows[0].call.tool_call_id=='legacy-call'
        assert any(tool['function']['name']=='mcp__vendor__lookup' for tool in model.bind_tools_calls[0]['tools'])
    finally:
        await service.aclose()


def test_direct_legacy_executor_cannot_bypass_confirmation(monkeypatch):
    from app import tools
    from app.executor import execute_tool_result
    calls=[]
    class Writer:
        def invoke(self,args):
            calls.append(args)
            return '工单创建成功'
    monkeypatch.setitem(tools.TOOL_REGISTRY,'create_ticket',Writer())
    result=execute_tool_result('create_ticket',{'description':'问题','ticket_type':'售后'},context={'conversation_id':42})
    assert calls==[] and result.status=='error'


async def test_app_lifespan_owns_tool_service_and_closes_it(monkeypatch):
    from app.main import create_app
    from app.config import get_settings
    settings=get_settings().model_copy(update={'chat_workflow_enabled':False})
    monkeypatch.setattr('app.main.get_settings',lambda:settings)
    app=create_app()
    async with app.router.lifespan_context(app):
        service=app.state.tool_service
        assert service.loop is asyncio.get_running_loop()
    assert app.state.tool_service is None


async def test_old_confirmed_button_uses_engine_and_audits_once(tmp_path,monkeypatch):
    from dataclasses import replace
    from tests.test_chat_actions import Runtime,Store,request
    from app.workflow.actions import confirm_ticket
    service,_,audit=make_service(tmp_path)
    await service.start()
    runtime=Runtime()
    runtime.tool_service=service
    calls=[]
    async def writer(args,ctx):
        assert runtime.actions['a']['status']=='creating'
        calls.append(args)
        return RawToolResult('工单创建成功,工单号 T202610260001')
    original=service.registry.resolve('builtin','create_ticket')
    service.registry.register(replace(original,handler=writer),replace=True)
    class LegacyTripwire:
        def invoke(self,args):
            raise AssertionError('legacy direct writer must not run')
    monkeypatch.setattr('app.workflow.actions.tools.create_ticket',LegacyTripwire())
    try:
        first=await confirm_ticket(request(),runtime=runtime,store=Store())
        repeated=await confirm_ticket(request(),runtime=runtime,store=Store())
        assert first.result==repeated.result and len(calls)==1
        assert len(audit.rows)==1 and audit.rows[0].call.name=='create_ticket'
        assert calls[0]['conversation_id']==42
    finally:
        await service.aclose()


def test_builtin_catalog_no_longer_advertises_local_logistics():
    from app.tools import get_tools
    assert 'query_logistics' not in {tool.name for tool in get_tools()}


async def test_legacy_query_plan_is_trusted_injected_context_not_model_argument(tmp_path,monkeypatch):
    from langchain_core.tools import tool
    from app.tools import QueryFaqArgs
    from app.kb.contracts import QueryPlan
    from tests.test_workflow_knowledge import result
    seen=[]
    @tool(args_schema=QueryFaqArgs,response_format='content_and_artifact')
    def query_faq(keyword:str,query_plan=None)->tuple:
        '''检索测试知识。'''
        seen.append(query_plan)
        return '规则',result(.8)
    monkeypatch.setattr('app.tools.query_faq',query_faq)
    service,_,_=make_service(tmp_path)
    await service.start()
    try:
        snapshot=await service.refresh()
        plan=QueryPlan(raw_query='退货',normalized_query='退货',lexical_query='退货')
        execution=await service.execute('query_faq',{'keyword':'退货'},context={'query_plan':plan},snapshot=snapshot)
        assert execution.status=='success' and seen==[plan]
    finally:
        await service.aclose()


async def test_bare_loop_validates_faq_before_any_retrieval(tmp_path,monkeypatch):
    from app.workflow.agent import run_agent_loop
    from app.tooling.scope import tool_scope
    service,_,audit=make_service(tmp_path)
    await service.start()
    seen=[]
    monkeypatch.setattr('app.kb.retriever.retrieve_result',lambda *args,**kwargs:seen.append(True))
    gateway=Gateway(rounds=[[{'name':'query_faq','args':{'keyword':123},'id':'bad-faq'}],
                            [{'name':'respond_to_user','args':{'response_mode':'answer'},'id':'respond'}]])
    try:
        snapshot=await service.refresh()
        with tool_scope(service,snapshot,{'session_id':'s','conversation_id':42,'request_id':'r'}):
            result=await run_agent_loop('政策',gateway=gateway,execute=lambda *a,**k:None,context={'conversation_id':42})
        assert result['answer'] and seen==[]
        assert audit.rows[0].result.outcome=='invalid_arguments'
    finally:
        await service.aclose()


async def test_sync_bridge_uses_owner_loop_and_refuses_owner_thread_block(tmp_path):
    service,manager,audit=make_service(tmp_path)
    await service.start()
    try:
        snapshot=await service.refresh()
        call=ToolCall("mcp__vendor__lookup",{"id":"1001"},"c","a"*32)
        ctx=ExecutionContext("s",42,"r",time.monotonic()+3)
        result=await asyncio.to_thread(service.run_sync,call,ctx,snapshot)
        assert result.status=="success" and len(manager.calls)==1
        rejected=service.run_sync(call,ctx,snapshot)
        assert rejected.status=="error" and len(manager.calls)==1
    finally:
        await service.aclose()


async def test_service_preserves_call_identity_and_denies_unconfirmed_write(tmp_path):
    service,manager,audit=make_service(tmp_path)
    await service.start()
    try:
        snapshot=await service.refresh()
        result=await service.execute("create_ticket",{"description":"未收到货","ticket_type":"售后"},
            context={"session_id":"s","conversation_id":42,"request_id":"r"},tool_call_id="ticket-call",snapshot=snapshot)
        assert result.outcome=="permission_denied"
        assert audit.rows[0].call.tool_call_id=="ticket-call"
    finally:
        await service.aclose()

