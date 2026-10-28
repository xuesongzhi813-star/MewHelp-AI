"""Best-effort terminal audits over the existing table, independently of tool transactions."""
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from functools import lru_cache
from sqlalchemy import create_engine, insert, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from app.models import ToolAuditLog
from app.tooling.validation import json_text

logger = logging.getLogger(__name__)
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tool-audit")
_SLOTS = threading.BoundedSemaphore(2)
_SECRET = re.compile(r"password|passwd|secret|api.?key|authorization|access.?token|db.?url", re.I)
_URL = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s/@:]+:[^\s/@]+@[^\s]+")


def _text(value, limit):
    if not isinstance(value, str):
        value = str(value) if value is None or isinstance(value, (int,float,bool)) else "[非文本]"
    return _URL.sub("[已脱敏连接串]", value)[:limit]


def _redact(value, depth=0):
    if depth > 10:
        return "[层级已截断]"
    if isinstance(value, dict):
        return {_text(k,128): "[已脱敏]" if _SECRET.search(str(k)) else _redact(v,depth+1)
                for k,v in list(value.items())[:128]}
    if isinstance(value, list):
        return [_redact(v,depth+1) for v in value[:128]]
    if isinstance(value,str):
        return _text(value,4000)
    if value is None or isinstance(value,(int,float,bool)):
        try:
            json.dumps(value,allow_nan=False)
            return value
        except ValueError:
            return "[非JSON数值]"
    return "[非JSON类型]"


def _identifier(value,limit):
    value = _text(value,8192)
    return value if len(value)<=limit else value[:limit-33]+"-"+hashlib.sha256(value.encode()).hexdigest()[:32]


def _number(value,upper):
    try:
        return max(0,min(upper,int(value)))
    except (ValueError,TypeError,OverflowError):
        return 0


def build_audit_row(call,ctx,result):
    meta=ctx.trusted
    source=meta.get("audit_source","builtin")
    server=meta.get("audit_server") if source=="mcp" else None
    arguments=_redact(meta.get('audit_arguments',call.args))
    if len(json_text(arguments).encode("utf-8"))>32768:
        arguments={"truncated":True,"summary":_text(json_text(arguments),4000)}
    summary={"invocation_key":_text(call.invocation_key,128),"request_id":_text(ctx.request_id,128),
             "outcome":_text(result.outcome,64),"summary":_text(result.content,2000),
             "source_resolution":meta.get("audit_source_resolution","unregistered")}
    if meta.get("audit_remote_name"):
        summary["remote_name"]=_text(meta["audit_remote_name"],128)
    if result.error_code:
        summary["error_code"]=_text(result.error_code,64)
    if isinstance(call.tool_call_id,str) and len(call.tool_call_id)>64:
        summary["original_tool_call_id"]=_text(call.tool_call_id,128)
    states={"invalid_arguments":"校验拦下","permission_denied":"权限拒绝","timeout":"超时","execution_error":"失败"}
    cid=ctx.conversation_id
    return {"conversation_id":cid if type(cid) is int and 0<cid<2**64 else None,
            "tool_call_id":_identifier(call.tool_call_id,64) if call.tool_call_id is not None else None,
            "tool_name":_identifier(call.name,128),"tool_source":source if source in {"builtin","mcp"} else "builtin",
            "mcp_server":_text(server,64) if server else None,"arguments":arguments,"result_summary":json_text(summary),
            "status":states.get(result.outcome,"成功" if result.status=="success" else "失败"),
            "error_message":_text(result.content,512) if result.status!="success" else None,
            "retry_count":_number(result.retry_count,255),"duration_ms":_number(result.elapsed_ms,2**32-1)}


@lru_cache
def _factory(db_url):
    engine=create_engine(db_url,pool_size=2,max_overflow=0,pool_timeout=1,
                         connect_args={"connect_timeout":1,"read_timeout":1,"write_timeout":1})
    return sessionmaker(bind=engine,expire_on_commit=False)


class MySQLAuditSink:
    def __init__(self,session_factory=None,*,timeout=1.0):
        if session_factory is None:
            from app.config import get_settings
            session_factory=_factory(get_settings().db_url)
        self.session_factory,self.timeout=session_factory,timeout

    async def write(self,call,ctx,result):
        if not _SLOTS.acquire(blocking=False):
            logger.warning("tool audit busy invocation=%s",_text(call.invocation_key,64))
            return
        try:
            row=build_audit_row(call,ctx,result)
            future=asyncio.get_running_loop().run_in_executor(_POOL,self._worker,row)
        except BaseException:
            _SLOTS.release()
            raise
        future.add_done_callback(lambda f:f.exception() if not f.cancelled() else None)
        try:
            async with asyncio.timeout(self.timeout):
                await asyncio.shield(future)
        except Exception as exc:
            logger.warning("tool audit failed invocation=%s exception=%s",_text(call.invocation_key,64),type(exc).__name__)

    def _worker(self,row):
        try:
            self._write_terminal(row)
        finally:
            _SLOTS.release()

    def _write_terminal(self,row):
        bound=self.session_factory.kw["bind"]
        with (bound.connect() if isinstance(bound,Engine) else nullcontext(bound)) as conn:
            self._write_on_connection(conn,row)

    def _write_on_connection(self,conn,row):
        key=json.loads(row["result_summary"])["invocation_key"]
        lock_name="mewhelp.audit."+hashlib.sha256(key.encode()).hexdigest()[:32]
        acquired=False
        try:
            with (conn.begin_nested() if conn.in_transaction() else conn.begin()):
                acquired=conn.execute(text("SELECT GET_LOCK(:name,0)"),{"name":lock_name}).scalar()==1
                if not acquired:
                    raise RuntimeError("audit_lock_unavailable")
                params={name:row[name] for name in ("conversation_id","tool_call_id","tool_name","tool_source","mcp_server")}
                candidates=conn.execute(text(
                    "SELECT result_summary FROM tool_audit_logs "
                    "WHERE conversation_id <=> :conversation_id AND tool_call_id <=> :tool_call_id "
                    "AND tool_name=:tool_name AND tool_source=:tool_source AND mcp_server <=> :mcp_server"
                ),params).mappings().all()
                for candidate in candidates:
                    try:
                        if json.loads(candidate["result_summary"]).get("invocation_key")==key:
                            return
                    except (ValueError,TypeError,AttributeError):
                        continue
                conn.execute(insert(ToolAuditLog.__table__).values(**row))
        finally:
            if acquired:
                try:
                    if conn.execute(text("SELECT RELEASE_LOCK(:name)"),{"name":lock_name}).scalar()!=1:
                        conn.invalidate()
                except Exception:
                    conn.invalidate()

