"""Catch permission bypass, incorrect retries and misleading execution results."""
import asyncio
import json
import time
from dataclasses import replace
import pytest
from app.tooling.contracts import RawToolResult, RejectedCall
from tests.tooling_fakes import make_engine


async def test_invalid_arguments_block_execution_and_are_audited(tmp_path):
    engine,handler,audit,_,ctx,call,snapshot,_=make_engine(tmp_path)
    result=await engine.dispatch(replace(call,args={"id":123}),ctx,snapshot)
    assert result.outcome=="invalid_arguments" and result.status=="error"
    assert handler.calls==0
    assert len(audit.rows)==1 and audit.rows[0].result.retry_count==0


async def test_unknown_tool_denied_and_audited(tmp_path):
    engine,handler,audit,_,ctx,call,snapshot,_=make_engine(tmp_path)
    result=await engine.dispatch(replace(call,name="nonexistent"),ctx,snapshot)
    assert result.outcome=="permission_denied"
    assert handler.calls==0 and len(audit.rows)==1


async def test_missing_confirmation_blocks_ticket(tmp_path):
    engine,handler,audit,_,ctx,call,snapshot,_=make_engine(tmp_path,name="create_ticket",effect="write")
    call=replace(call,args={"description":"包裹未收到","ticket_type":"售后"})
    result=await engine.dispatch(call,ctx,snapshot)
    assert result.outcome=="permission_denied"
    assert handler.calls==0 and len(audit.rows)==1


async def test_server_write_is_denied_even_when_local_rule_enabled(tmp_path):
    engine,handler,audit,_,ctx,call,snapshot,_=make_engine(tmp_path,source="vendor",effect="write")
    result=await engine.dispatch(call,ctx,snapshot)
    assert result.outcome=="permission_denied"
    assert handler.calls==0 and len(audit.rows)==1


async def test_forged_system_parameters_never_reach_handler(tmp_path):
    engine,handler,audit,_,ctx,call,snapshot,_=make_engine(tmp_path)
    result=await engine.dispatch(replace(call,args={"id":"1001","confirmed":True}),ctx,snapshot)
    assert result.outcome=="invalid_arguments"
    assert handler.calls==0


async def test_revocation_after_prepare_still_blocks_execution(tmp_path):
    engine,handler,audit,_,ctx,call,snapshot,path=make_engine(tmp_path)
    prepared=engine.prepare(call,ctx,snapshot)
    path.write_text('{"servers":{},"rules":{"builtin/lookup":{"enabled":false,"effect":"read"}}}',encoding="utf-8")
    result=await engine.invoke(prepared,ctx)
    assert result.outcome=="permission_denied"
    assert handler.calls==0


async def test_endpoint_change_after_prepare_never_calls_new_server(tmp_path):
    engine,handler,audit,_,ctx,call,snapshot,path=make_engine(tmp_path,source="vendor")
    prepared=engine.prepare(call,ctx,snapshot)
    data=json.loads(path.read_text())
    data["servers"]["vendor"]["url"]="http://127.0.0.1:8022/mcp"
    path.write_text(json.dumps(data))
    assert (await engine.invoke(prepared,ctx)).outcome=="permission_denied"
    assert handler.calls==0


async def test_temporary_read_error_retries_once_and_audits_once(tmp_path):
    engine,handler,audit,_,ctx,call,snapshot,_=make_engine(tmp_path,outputs=[ConnectionError("private"),RawToolResult({"status":"ok"})])
    result=await engine.dispatch(call,ctx,snapshot)
    assert result.status=="success" and result.retry_count==1
    assert handler.calls==2 and len(audit.rows)==1


@pytest.mark.parametrize("output,outcome",[
    (RuntimeError("private-url"),"execution_error"),
    (RawToolResult({"found":False},"unused","not_found"),"not_found"),
])
async def test_permanent_error_or_empty_result_never_retries(tmp_path,output,outcome):
    engine,handler,audit,_,ctx,call,snapshot,_=make_engine(tmp_path,outputs=[output])
    result=await engine.dispatch(call,ctx,snapshot)
    assert result.outcome==outcome
    assert result.retry_count==0 and handler.calls==1
    assert "private-url" not in result.content


async def test_audit_failure_never_changes_business_success(tmp_path):
    engine,handler,_,_,ctx,call,snapshot,_=make_engine(tmp_path,audit_fail=True)
    result=await engine.dispatch(call,ctx,snapshot)
    assert result.status=="success" and handler.calls==1


async def test_expired_deadline_does_not_start_handler(tmp_path):
    engine,handler,audit,_,ctx,call,snapshot,_=make_engine(tmp_path)
    result=await engine.dispatch(call,replace(ctx,deadline=time.monotonic()-1),snapshot)
    assert result.outcome=="timeout" and handler.calls==0


def ticket_context(ctx):
    draft={"description":"包裹未收到","ticket_type":"售后"}
    return replace(ctx,trusted={"ticket_requested":True,"ticket_draft":draft}),draft


async def test_confirmed_ticket_executes_once_and_system_injects_conversation(tmp_path):
    engine,handler,audit,caps,ctx,call,snapshot,_=make_engine(tmp_path,name="create_ticket",effect="write")
    ctx,draft=ticket_context(ctx)
    call=replace(call,args=draft)
    prepared=engine.prepare(call,ctx,snapshot)
    assert not isinstance(prepared,RejectedCall)
    nonce=caps.issue(ctx.session_id,call.invocation_key,prepared.args_hash,snapshot.policy_hash)
    first=await engine.invoke(prepared,ctx,nonce=nonce)
    second=await engine.invoke(prepared,ctx,nonce=nonce)
    assert first.status=="success" and second.outcome=="permission_denied"
    assert handler.calls==1
    assert handler.arguments[0]["conversation_id"]==42


async def test_write_timeout_is_one_attempt_and_consumes_capability(tmp_path):
    engine,handler,audit,caps,ctx,call,snapshot,_=make_engine(tmp_path,name="create_ticket",effect="write",outputs=[TimeoutError("uncertain")])
    ctx,draft=ticket_context(ctx)
    call=replace(call,args=draft)
    prepared=engine.prepare(call,ctx,snapshot)
    nonce=caps.issue(ctx.session_id,call.invocation_key,prepared.args_hash,snapshot.policy_hash)
    result=await engine.invoke(prepared,ctx,nonce=nonce)
    assert result.outcome=="timeout" and result.retry_count==0 and handler.calls==1
    assert "核实" in result.content
    assert (await engine.invoke(prepared,ctx,nonce=nonce)).outcome=="permission_denied"


async def test_model_cannot_replace_verified_ticket_description(tmp_path):
    engine,handler,_,_,ctx,call,snapshot,_=make_engine(tmp_path,name="create_ticket",effect="write")
    ctx,_=ticket_context(ctx)
    result=await engine.dispatch(replace(call,args={"description":"编造的退款要求","ticket_type":"售后"}),ctx,snapshot)
    assert result.outcome=="invalid_arguments" and handler.calls==0


async def test_inflight_timeout_is_bounded_without_sleep_retry(tmp_path):
    engine,_,audit,_,ctx,call,snapshot,_=make_engine(tmp_path)
    waiting=asyncio.Event()
    async def blocked(args,ctx):
        await waiting.wait()
        return RawToolResult({})
    definition=engine.registry.resolve("builtin","lookup")
    engine.registry.register(replace(definition,handler=blocked),replace=True)
    snapshot=engine.registry.snapshot(engine.policy_loader.load())
    result=await engine.dispatch(call,replace(ctx,deadline=time.monotonic()+0.02),snapshot)
    assert result.outcome=="timeout" and len(audit.rows)==1

async def test_definition_change_after_prepare_never_runs_old_handler(tmp_path):
    engine,handler,_,_,ctx,call,snapshot,_=make_engine(tmp_path)
    prepared=engine.prepare(call,ctx,snapshot)
    definition=engine.registry.resolve("builtin","lookup")
    definition.input_schema["properties"]["id"]["maxLength"]=1
    engine.registry.register(definition,replace=True)
    assert (await engine.invoke(prepared,ctx)).outcome=="permission_denied"
    assert handler.calls==0


async def test_builtin_adapter_has_no_local_logistics_and_preserves_faq_artifact(monkeypatch):
    from langchain_core.tools import tool
    from app import tools
    from app.tooling.builtin import register_builtins
    from app.tooling.registry import ToolRegistry
    from app.tooling.policy import PolicySnapshot
    from app.tooling.contracts import ExecutionContext
    artifact=object()
    @tool(response_format="content_and_artifact")
    def faq(keyword:str)->tuple:
        """查询测试知识。"""
        return "1. 问题→答案",artifact
    monkeypatch.setattr(tools,"query_faq",faq)
    registry=ToolRegistry()
    register_builtins(registry)
    assert registry.resolve("builtin","query_logistics") is None
    ctx=ExecutionContext("s",42,"r",time.monotonic()+5)
    output=await registry.resolve("builtin","faq").handler({"keyword":"退货"},ctx)
    assert output.content=="1. 问题→答案" and output.artifact is artifact


def test_builtin_ticket_schema_rejects_oversized_description():
    from app.tooling.builtin import register_builtins
    from app.tooling.registry import ToolRegistry
    from app.tooling.validation import validate_arguments
    registry=ToolRegistry()
    register_builtins(registry)
    schema=registry.resolve("builtin","create_ticket").input_schema
    assert validate_arguments(schema,{'description':'问题','ticket_type':'售后','conversation_id':42})
    assert not validate_arguments(schema,{'description':'字'*2000,'ticket_type':'售后'})
    assert validate_arguments(schema,{"description":"字"*2001,"ticket_type":"售后"})


async def test_cancelled_builtin_wait_does_not_cancel_queued_work(monkeypatch):
    import concurrent.futures
    import threading
    from app.tooling import builtin
    from app.tooling.registry import ToolRegistry
    from app.tooling.contracts import ExecutionContext
    submitted=asyncio.Event()
    future=concurrent.futures.Future()
    class Pool:
        def submit(self,fn,*args):
            self.job=lambda:fn(*args)
            submitted.set()
            return future
    pool=Pool()
    monkeypatch.setattr(builtin,"_POOL",pool)
    monkeypatch.setattr(builtin,"_SLOTS",threading.BoundedSemaphore(4))
    registry=ToolRegistry()
    builtin.register_builtins(registry)
    ctx=ExecutionContext("s",42,"r",time.monotonic()+5)
    task=asyncio.create_task(registry.resolve("builtin","query_order").handler({"order_id":"1001"},ctx))
    await submitted.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not future.cancelled()
    future.set_result(pool.job())


async def test_late_builtin_exception_is_retrieved_after_waiter_cancel(monkeypatch):
    import concurrent.futures
    import gc
    import threading
    from app.tooling import builtin
    from app.tooling.registry import ToolRegistry
    from app.tooling.contracts import ExecutionContext
    loop=asyncio.get_running_loop()
    original=loop.get_exception_handler()
    errors=[]
    loop.set_exception_handler(lambda loop,context:errors.append(context))
    async def cancelled_waiter():
        future=concurrent.futures.Future()
        submitted=asyncio.Event()
        class Pool:
            def submit(self,*args):
                submitted.set()
                return future
        with monkeypatch.context() as patch:
            patch.setattr(builtin,'_POOL',Pool())
            patch.setattr(builtin,'_SLOTS',threading.BoundedSemaphore(4))
            registry=ToolRegistry()
            builtin.register_builtins(registry)
            ctx=ExecutionContext('s',42,'r',time.monotonic()+5)
            task=asyncio.create_task(registry.resolve('builtin','query_order').handler({'order_id':'1001'},ctx))
            await submitted.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            future.set_exception(RuntimeError('private late failure'))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
    try:
        await cancelled_waiter()
        gc.collect()
        await asyncio.sleep(0)
        assert not errors
    finally:
        loop.set_exception_handler(original)


