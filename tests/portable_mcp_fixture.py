"""Real MCP tool with a separately excluded tool for portability acceptance."""

try:
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:
    from mcp.server.fastmcp import FastMCP

server = FastMCP("portable")


@server.tool()
def slow() -> str:
    """Validate the previously looked-up order."""
    return "Validated USD 12"


@server.tool()
def forbidden() -> str:
    """Must never be offered to the model."""
    raise AssertionError("The MCP allowlist was not enforced")


if __name__ == "__main__":
    server.run()
