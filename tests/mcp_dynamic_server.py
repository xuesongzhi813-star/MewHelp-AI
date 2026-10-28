"""Test-only dynamic MCP catalog. Never mounted by the product."""
import os
from typing import Any
from mcp.server.fastmcp import FastMCP
from app.mcp_servers.common import cli

args=cli(0)
server=FastMCP("Ch08 Dynamic Test",host=args.host,port=args.port,stateless_http=True,json_response=True)

@server.tool()
def first(id:str)->dict[str,Any]:
    """查询首个演示工具。"""
    return {"id":id,"server_pid":os.getpid()}

@server.tool()
def test_add()->dict[str,Any]:
    """仅测试：在本进程新增工具。"""
    def added(id:str)->dict[str,Any]:
        return {"id":id,"server_pid":os.getpid()}
    server.add_tool(added,name="added",description="查询新增演示工具")
    return {"server_pid":os.getpid()}

@server.tool()
def test_remove()->dict[str,Any]:
    """仅测试：删除新增工具。"""
    server.remove_tool("added")
    return {"server_pid":os.getpid()}

if __name__=="__main__":
    server.run(transport="streamable-http")

