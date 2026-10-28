"""Real dynamic tool discovery without restarting the client or MCP server."""
import asyncio
import json
from pathlib import Path
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from tests.test_mcp_servers import running_server
from tests.tooling_fakes import FakeAuditSink
from app.tooling.policy import PolicyLoader
from app.tooling.service import ToolService
from app.tooling.contracts import ToolCall,ExecutionContext


async def test_live_add_authorize_remove_tools_without_restarting_service(tmp_path):
    async with running_server("tests.mcp_dynamic_server",tmp_path) as url:
        path=tmp_path/"policy.json"
        data={"servers":{"vendor":{"url":url,"enabled":True}},
              "rules":{"vendor/first":{"enabled":True,"effect":"read","output_fields":["id","server_pid"]}}}
        path.write_text(json.dumps(data),encoding="utf-8")
        service=ToolService(PolicyLoader(path),audit_sink=FakeAuditSink())
        await service.start()
        try:
            before=await service.refresh()
            assert "mcp__vendor__first" in [s["function"]["name"] for s in before.schemas]
            async with streamable_http_client(url) as (read,write,_):
                async with ClientSession(read,write) as admin:
                    await admin.initialize()
                    added=await admin.call_tool("test_add",{})
                    pid=added.structuredContent["server_pid"]
                    unauthorized=await service.refresh()
                    assert "mcp__vendor__added" not in [s["function"]["name"] for s in unauthorized.schemas]
                    data["rules"]["vendor/added"]={"enabled":True,"effect":"read","output_fields":["id","server_pid"]}
                    path.write_text(json.dumps(data),encoding="utf-8")
                    after=await service.refresh()
                    concurrent=await asyncio.gather(service.refresh(),service.refresh())
                    assert concurrent[0].version==concurrent[1].version==after.version
                    assert after.version!=before.version
                    assert "mcp__vendor__added" not in [s["function"]["name"] for s in before.schemas]
                    ctx=ExecutionContext("s",42,"r",asyncio.get_running_loop().time()+3)
                    result=await service.engine.dispatch(ToolCall("mcp__vendor__added",{"id":"1001"},"call1","a"*32),ctx,after)
                    assert json.loads(result.content)=={"id":"1001","server_pid":pid}
                    removed=await admin.call_tool("test_remove",{})
                    assert removed.structuredContent["server_pid"]==pid
                    final=await service.refresh()
                    assert "mcp__vendor__added" not in [s["function"]["name"] for s in final.schemas]
                    path.write_text("{broken",encoding="utf-8")
                    closed=await service.refresh()
                    assert "query_order" in [s["function"]["name"] for s in closed.schemas]
                    assert not any(s["function"]["name"].startswith("mcp__") for s in closed.schemas)
        finally:
            await service.aclose()


async def test_server_restarts_at_same_endpoint_without_restarting_client(tmp_path):
    from urllib.parse import urlsplit
    service=None
    try:
        async with running_server("tests.mcp_dynamic_server",tmp_path) as url:
            port=urlsplit(url).port
            path=tmp_path/"restart-policy.json"
            path.write_text(json.dumps({"servers":{"vendor":{"url":url,"enabled":True}},
                "rules":{"vendor/first":{"enabled":True,"effect":"read","output_fields":["id","server_pid"]}}}))
            service=ToolService(PolicyLoader(path),audit_sink=FakeAuditSink())
            await service.start()
            first=await service.refresh()
            ctx=ExecutionContext("s",42,"r",asyncio.get_running_loop().time()+3)
            result=await service.engine.dispatch(ToolCall("mcp__vendor__first",{"id":"1001"},"c1","1"*32),ctx,first)
            first_pid=json.loads(result.content)["server_pid"]
        offline=await service.refresh()
        assert "mcp__vendor__first" not in [s["function"]["name"] for s in offline.schemas]
        async with running_server("tests.mcp_dynamic_server",tmp_path,port=port):
            restored=await service.refresh()
            ctx=ExecutionContext("s",42,"r",asyncio.get_running_loop().time()+3)
            result=await service.engine.dispatch(ToolCall("mcp__vendor__first",{"id":"1001"},"c2","2"*32),ctx,restored)
            assert result.status=="success"
            assert json.loads(result.content)["server_pid"]!=first_pid
    finally:
        if service is not None:
            await service.aclose()

