"""Disposable official MCP server used only by transport integration tests."""

import asyncio
import sys

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:  # MCP 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer

server = MCPServer("liteagents-test")


@server.tool()
async def read_memory(query: str) -> str:
    """Read synthetic evidence."""
    if query == "fail":
        raise ValueError("synthetic failure")
    if query == "slow":
        await asyncio.sleep(30)
    return "evidence:" + query


@server.tool()
async def write_memory(text: str) -> str:
    """A tool that the test client must never expose."""
    raise AssertionError("Write must not be invoked")


if __name__ == "__main__":
    if len(sys.argv) == 1:
        server.run(transport="stdio")
    else:
        import uvicorn

        app = server.sse_app() if len(sys.argv) > 2 and sys.argv[2] == "sse" else server.streamable_http_app()

        async def authenticated(scope, receive, send):
            if scope["type"] == "http" and dict(scope["headers"]).get(b"authorization") != b"Bearer synthetic":
                await send({"type": "http.response.start", "status": 401, "headers": []})
                await send({"type": "http.response.body", "body": b"Unauthorized"})
                return
            await app(scope, receive, send)

        # Old MCP SSE handlers can retain a long-lived connection after the
        # client exits. Bound graceful shutdown of this disposable fixture.
        uvicorn.run(authenticated, host="127.0.0.1", port=int(sys.argv[1]), log_level="error",
                    timeout_graceful_shutdown=1)
