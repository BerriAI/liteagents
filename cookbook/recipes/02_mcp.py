"""Let any harness discover and call a real local MCP server."""

import asyncio
import sys
from pathlib import Path

from _common import parser, setup

from liteagents import LiteAgentClient


async def main():
    args = parser(__doc__).parse_args()
    cwd, profile = setup(args, "mcp", tools=["orders_lookup_order"])
    profile.mcp_servers = {
        "orders": {
            "command": sys.executable,
            "args": [str(Path(__file__).with_name("mcp_server.py"))],
            "allowed_tools": ["lookup_order"],
        }
    }
    async with LiteAgentClient(profile=profile, cwd=cwd) as client:
        run = await client.start_run(
            "Call orders_lookup_order for order A123 and report its total and payment status."
        )
        result = await run.result()
        assert "12" in result.text, result.text
        print(result.text)


if __name__ == "__main__":
    asyncio.run(main())
