"""Independent synthetic logistics MCP server: python -m app.mcp_servers.logistics."""
from mcp.server.fastmcp import FastMCP
from typing import Any
from app.mcp_servers.common import MockBusinessData, OrderId, cli


def build_server(host="127.0.0.1", port=8021):
    server = FastMCP("MewHelp Logistics", host=host, port=port, stateless_http=True, json_response=True)
    data = MockBusinessData()

    @server.tool()
    def query_logistics(order_id: OrderId) -> dict[str, Any]:
        """按订单号查询演示物流轨迹、承运商、当前节点和预计送达日期。"""
        return data.logistics(order_id)

    return server


if __name__ == "__main__":
    args = cli(8021)
    build_server(args.host, args.port).run(transport="streamable-http")

