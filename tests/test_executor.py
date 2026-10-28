"""Public synchronous compatibility APIs share the async engine's safety semantics."""
from concurrent.futures import ThreadPoolExecutor
import pytest

import app.executor as executor
from app.executor import execute_tool,execute_tool_result
from app.tooling.contracts import RawToolResult
from app.tooling.service import ToolService
from tests.tooling_fakes import make_engine


class NoTransport:
    async def start(self): pass
    async def aclose(self): pass


def bind(tmp_path,monkeypatch,outputs=None):
    engine,handler,audit,*_=make_engine(tmp_path,outputs=outputs)
    monkeypatch.setattr(executor,'_local_service',lambda:ToolService(
        engine.policy_loader,registry=engine.registry,manager=NoTransport(),audit_sink=audit))
    return handler,audit


def test_execute_tool_returns_string_result_as_is(tmp_path,monkeypatch):
    handler,audit=bind(tmp_path,monkeypatch,[RawToolResult('echo-ok')])
    assert execute_tool('lookup',{'id':'1001'})=='echo-ok'
    assert handler.calls==1 and len(audit.rows)==1


def test_unknown_tool_is_denied_with_correction_hint(tmp_path,monkeypatch):
    handler,audit=bind(tmp_path,monkeypatch)
    result=execute_tool_result('missing',{})
    assert result.outcome=='permission_denied' and handler.calls==0
    assert 'missing' in result.content and 'lookup' in result.content
    assert len(audit.rows)==1


@pytest.mark.parametrize('args',[{},[],{'id':123},{'id':'1001','conversation_id':99}])
def test_invalid_parameters_never_reach_handler(tmp_path,monkeypatch,args):
    handler,audit=bind(tmp_path,monkeypatch)
    result=execute_tool_result('lookup',args)
    assert result.outcome=='invalid_arguments' and handler.calls==0
    assert audit.rows[0].result.retry_count==0


def test_transient_read_error_retries_once(tmp_path,monkeypatch):
    handler,audit=bind(tmp_path,monkeypatch,[ConnectionError('private-address'),RawToolResult('recovered')])
    result=execute_tool_result('lookup',{'id':'1001'})
    assert result.content=='recovered' and result.retry_count==1 and handler.calls==2
    assert len(audit.rows)==1


def test_permanent_error_does_not_retry_or_leak_trace(tmp_path,monkeypatch):
    handler,_=bind(tmp_path,monkeypatch,[RuntimeError('private-address')])
    result=execute_tool_result('lookup',{'id':'1001'})
    assert result.outcome=='execution_error' and handler.calls==1
    assert 'private-address' not in result.content and 'RuntimeError' not in result.content


def test_timeout_has_at_most_one_transient_read_retry(tmp_path,monkeypatch):
    handler,_=bind(tmp_path,monkeypatch,[TimeoutError('private')])
    result=execute_tool_result('lookup',{'id':'1001'})
    assert result.outcome=='timeout' and result.retry_count==1 and handler.calls==2


def test_system_context_is_separate_from_model_parameters(tmp_path,monkeypatch):
    handler,audit=bind(tmp_path,monkeypatch)
    result=execute_tool_result('lookup',{'id':'1001'},context={'conversation_id':42})
    assert result.status=='success'
    assert handler.arguments==[{'id':'1001'}] and audit.rows[0].ctx.conversation_id==42


def test_unhashable_name_returns_error_instead_of_raising(tmp_path,monkeypatch):
    handler,_=bind(tmp_path,monkeypatch)
    result=execute_tool_result(['invalid'],{})
    assert result.status=='error' and handler.calls==0


def test_shutdown_builtin_pool_is_reported_without_trace(monkeypatch):
    import app.tooling.builtin as builtin
    pool=ThreadPoolExecutor(max_workers=1)
    pool.shutdown(wait=True)
    monkeypatch.setattr(builtin,'_POOL',pool)
    result=execute_tool_result('query_order',{'order_id':'1001'})
    assert result.outcome=='execution_error' and result.retry_count==0
    assert 'ThreadPoolExecutor' not in result.content


def test_construction_failure_keeps_string_contract(monkeypatch):
    def broken():
        raise TypeError('private-path')
    monkeypatch.setattr(executor,'_local_service',broken)
    result=execute_tool('query_order',{'order_id':'1001'})
    assert isinstance(result,str) and 'private-path' not in result


async def test_sync_api_refuses_to_block_an_active_event_loop():
    result=execute_tool_result('query_order',{'order_id':'1001'})
    assert result.status=='error' and '事件循环' in result.content
