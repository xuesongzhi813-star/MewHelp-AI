"""Audit mappings and failure isolation against the existing table contract."""
import json
from dataclasses import replace
import pytest
from sqlalchemy.orm import sessionmaker
from app.tooling.audit import MySQLAuditSink,build_audit_row
from app.tooling.contracts import ToolCall,ExecutionContext,ToolExecutionResult
from app.models import ToolAuditLog


def inputs():
    return ToolCall("query_logistics",{"order_id":"1001","password":"secret"},"call1","a"*32),ExecutionContext(
        "s",42,"r",123,{"audit_source":"mcp","audit_server":"logistics","audit_remote_name":"query_logistics",
                        "audit_source_resolution":"registered"})


def test_row_maps_existing_chinese_status_and_redacts_parameters():
    call,ctx=inputs()
    result=ToolExecutionResult("运输中",outcome="not_found",retry_count=1,elapsed_ms=12)
    row=build_audit_row(call,ctx,result)
    assert row["status"]=="成功" and row["retry_count"]==1 and row["duration_ms"]==12
    assert row["tool_source"]=="mcp" and row["mcp_server"]=="logistics"
    assert row["arguments"]["password"]=="[已脱敏]"
    assert "secret" not in json.dumps(row,ensure_ascii=False)
    assert json.loads(row["result_summary"])["invocation_key"]=="a"*32
    assert "\\u" not in row["result_summary"]


@pytest.mark.parametrize("outcome,status",[
    ("invalid_arguments","校验拦下"),("permission_denied","权限拒绝"),
    ("timeout","超时"),("execution_error","失败"),
])
def test_error_outcomes_map_to_real_database_enum(outcome,status):
    call,ctx=inputs()
    row=build_audit_row(call,ctx,ToolExecutionResult("失败",status="error",outcome=outcome))
    assert row["status"]==status


def test_long_ids_and_errors_are_bounded_without_prefix_collision():
    call,ctx=inputs()
    first=build_audit_row(replace(call,tool_call_id="a"*100+"1"),ctx,
        ToolExecutionResult("错"*1000,status="error",outcome="execution_error",elapsed_ms=-1))
    second=build_audit_row(replace(call,tool_call_id="a"*100+"2"),ctx,ToolExecutionResult("ok"))
    assert len(first["tool_call_id"])<=64 and first["tool_call_id"]!=second["tool_call_id"]
    assert len(first["error_message"])<=512 and first["duration_ms"]==0


def test_model_does_not_invent_missing_columns_or_foreign_keys():
    table=ToolAuditLog.__table__
    assert set(table.columns.keys())=={"id","conversation_id","tool_call_id","tool_name","tool_source","mcp_server",
        "arguments","result_summary","status","error_message","retry_count","duration_ms","created_at"}
    assert not table.foreign_keys
    assert not table.c.status.nullable


class Result:
    def __init__(self,value=None,rows=None):
        self.value=value
        self.rows=rows or []
    def scalar(self):
        return self.value
    def mappings(self):
        return self
    def all(self):
        return self.rows


class Transaction:
    def __init__(self,conn):
        self.conn=conn
    def __enter__(self):
        return self
    def __exit__(self,kind,value,tb):
        if kind is None and self.conn.fault=="commit":
            raise RuntimeError("private commit error")
        return False


class Connection:
    def __init__(self,fault=None,lock=1):
        self.rows=[]
        self.fault=fault
        self.lock=lock
        self.invalidated=False
        self.releases=0
    def in_transaction(self):
        return False
    def begin(self):
        return Transaction(self)
    def begin_nested(self):
        return Transaction(self)
    def execute(self,statement,params=None):
        sql=str(statement)
        if "GET_LOCK" in sql:
            if self.fault=="lock":
                raise RuntimeError("private lock error")
            return Result(self.lock)
        if "RELEASE_LOCK" in sql:
            self.releases+=1
            if self.fault=="release":
                raise RuntimeError("private release error")
            return Result(1)
        if sql.lstrip().startswith("SELECT"):
            if self.fault=="select":
                raise RuntimeError("private select error")
            return Result(rows=[{"result_summary":r["result_summary"]} for r in self.rows])
        if self.fault=="insert":
            raise RuntimeError("private insert error")
        self.rows.append(dict(statement.compile().params))
        return Result()
    def invalidate(self):
        self.invalidated=True


def sink_for(connection):
    return MySQLAuditSink(sessionmaker(bind=connection))


async def test_repeated_terminal_write_deduplicates_same_invocation():
    conn=Connection()
    sink=sink_for(conn)
    call,ctx=inputs()
    await sink.write(call,ctx,ToolExecutionResult("运输中"))
    await sink.write(call,ctx,ToolExecutionResult("运输中"))
    assert len(conn.rows)==1 and conn.releases==2


@pytest.mark.parametrize("fault",["lock","select","insert","commit","release"])
async def test_audit_database_failure_never_escapes(fault):
    conn=Connection(fault=fault)
    call,ctx=inputs()
    await sink_for(conn).write(call,ctx,ToolExecutionResult("ok"))
    if fault!="lock":
        assert conn.releases==1
    if fault=="release":
        assert conn.invalidated


@pytest.mark.parametrize("lock",[0,None])
async def test_unavailable_lock_never_inserts_or_releases_other_owner(lock):
    conn=Connection(lock=lock)
    call,ctx=inputs()
    await sink_for(conn).write(call,ctx,ToolExecutionResult("ok"))
    assert conn.rows==[] and conn.releases==0


async def test_commit_success_then_client_error_does_not_duplicate_on_retry():
    conn=Connection(fault="commit")
    sink=sink_for(conn)
    call,ctx=inputs()
    await sink.write(call,ctx,ToolExecutionResult("ok"))
    conn.fault=None
    await sink.write(call,ctx,ToolExecutionResult("ok"))
    assert len(conn.rows)==1


async def test_engine_supplies_trusted_mcp_source_to_audit_sink(tmp_path):
    from tests.tooling_fakes import make_engine
    engine,handler,audit,_,ctx,call,snapshot,_=make_engine(tmp_path,source="vendor")
    await engine.dispatch(call,ctx,snapshot)
    event=audit.rows[0]
    row=build_audit_row(event.call,event.ctx,event.result)
    assert row["tool_source"]=="mcp" and row["mcp_server"]=="vendor"
    assert json.loads(row["result_summary"])["remote_name"]=="lookup"

