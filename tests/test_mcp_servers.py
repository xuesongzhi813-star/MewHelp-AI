"""Real independent MCP HTTP processes, not REST stubs."""
import asyncio
import os
from pathlib import Path
import socket
import subprocess
import sys
from contextlib import asynccontextmanager

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

ROOT=Path(__file__).resolve().parents[1]


@asynccontextmanager
async def running_server(module,tmp_path,*,port=None):
    if port is None:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1",0))
            port=listener.getsockname()[1]
    logfile=(tmp_path/(module.rsplit(".",1)[-1]+".log")).open("w",encoding="utf-8")
    proc=subprocess.Popen([sys.executable,"-m",module,"--host","127.0.0.1","--port",str(port)],
        cwd=ROOT,stdout=logfile,stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
    try:
        async with asyncio.timeout(15):
            while True:
                if proc.poll() is not None:
                    raise RuntimeError("test MCP process exited; see temporary server log")
                try:
                    reader,writer=await asyncio.open_connection("127.0.0.1",port)
                    writer.close()
                    await writer.wait_closed()
                    break
                except OSError:
                    await asyncio.sleep(0.025)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                await asyncio.to_thread(proc.wait,5)
            except subprocess.TimeoutExpired:
                proc.kill()
                await asyncio.to_thread(proc.wait,5)
        logfile.close()


@pytest.mark.parametrize("module,names",[
    ("app.mcp_servers.logistics",{"query_logistics"}),
    ("app.mcp_servers.aftersales",{"query_warranty","query_return_progress"}),
])
async def test_independent_http_mcp_discovers_and_calls_business_tools(tmp_path,module,names):
    async with running_server(module,tmp_path) as url:
        async with streamable_http_client(url) as (read,write,_):
            async with ClientSession(read,write) as session:
                await session.initialize()
                discovered=await session.list_tools()
                assert {tool.name for tool in discovered.tools}==names
                for tool in discovered.tools:
                    schema=tool.inputSchema
                    assert "order_id" in schema["required"]
                    assert schema["properties"]["order_id"]["minLength"]==1
                    assert schema["properties"]["order_id"]["maxLength"]==64
                    result=await session.call_tool(tool.name,{"order_id":"1001"})
                    assert not result.isError
                    data=result.structuredContent
                    assert data["order_id"]=="1001" and data["is_demo"] is True
                    invalid=await session.call_tool(tool.name,{"order_id":""})
                    assert invalid.isError

