"""Existing-table integration with an outer rollback; no DDL or table cleanup."""
import json
import uuid
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from app.db import get_engine
from app.models import ToolAuditLog
from app.tooling.audit import MySQLAuditSink
from app.tooling.contracts import ToolCall,ExecutionContext,ToolExecutionResult


async def test_real_mysql_audit_deduplicates_and_outer_transaction_rolls_back():
    key=uuid.uuid4().hex
    engine=get_engine()
    with engine.connect() as conn:
        outer=conn.begin()
        sink=MySQLAuditSink(sessionmaker(bind=conn))
        try:
            call=ToolCall("ch08_synthetic",{"keyword":"测试"},"ch08-"+key,key)
            ctx=ExecutionContext("ch08-synthetic",None,"ch08-test",123)
            await sink.write(call,ctx,ToolExecutionResult("查询落空",outcome="not_found",elapsed_ms=7))
            await sink.write(call,ctx,ToolExecutionResult("查询落空",outcome="not_found",elapsed_ms=7))
            rows=conn.execute(select(ToolAuditLog.__table__).where(ToolAuditLog.tool_call_id=="ch08-"+key)).mappings().all()
            assert len(rows)==1
            assert rows[0]["status"]=="成功" and rows[0]["duration_ms"]==7
            assert json.loads(rows[0]["result_summary"])["invocation_key"]==key
        finally:
            outer.rollback()
    with engine.connect() as conn:
        assert conn.execute(select(ToolAuditLog.id).where(ToolAuditLog.tool_call_id=="ch08-"+key)).all()==[]

