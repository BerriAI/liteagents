"""Local MCP demo server: no account or external service needed."""

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:  # MCP 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer

server = MCPServer("Orders")


@server.tool()
def lookup_order(order_id: str) -> dict:
    """Look up a demo order's payment status and total."""
    return {"order_id": order_id, "total_usd": 12, "status": "paid"}


if __name__ == "__main__":
    server.run()
