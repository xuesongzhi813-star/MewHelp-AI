"""Independent synthetic aftersales MCP server: python -m app.mcp_servers.aftersales."""
from mcp.server.fastmcp import FastMCP
from typing import Any
from app.mcp_servers.common import MockBusinessData, OrderId, cli


def build_server(host="127.0.0.1", port=8022):
    server = FastMCP("MewHelp Aftersales", host=host, port=port, stateless_http=True, json_response=True)
    data = MockBusinessData()

    @server.tool()
    def query_warranty(order_id: OrderId) -> dict[str, Any]:
        """按订单号查询演示售后在保记录；不代替实际店铺保障政策。"""
        return data.warranty(order_id)

    @server.tool()
    def query_return_progress(order_id: OrderId) -> dict[str, Any]:
        """按订单号查询已发起退货的演示处理进度；不会创建申请或真实退款。"""
        return data.return_progress(order_id)

    return server


if __name__ == "__main__":
    args = cli(8022)
    build_server(args.host, args.port).run(transport="streamable-http")

