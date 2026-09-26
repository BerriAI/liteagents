"""Keep one model, Python tool, MCP server, and conversation across harness choices."""

import asyncio
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

from _common import parser, setup

from liteagents import (
    AssistantMessage,
    LiteAgentClient,
    LiteAgentOptions,
    TemporalOptions,
    Tool,
    ToolUseBlock,
    available_harnesses,
)


class ShippingStatus(Tool):
    name = "shipping_status"
    description = "Return the shipping status of an order."
    input_schema: ClassVar[dict] = {
        "type": "object", "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"], "additionalProperties": False,
    }

    def __init__(self):
        self.calls = 0

    async def execute(self, input):
        self.calls += 1
        return f"Order {input['order_id']} has shipped. Tracking code COBALT-42."


async def application(profile, cwd, *, durable):
    tool = ShippingStatus()
    async with AsyncExitStack() as stack:
        if durable:
            from liteagents.temporal import LiteAgentWorker

            # The demo owns a worker for convenience. In a deployment it runs
            # separately with this same profile and application tool registry.
            await stack.enter_async_context(
                LiteAgentWorker(profile=profile, cwd=cwd, tools=[tool]).running()
            )
        client = await stack.enter_async_context(LiteAgentClient(
            options=LiteAgentOptions(profile=profile, cwd=cwd, tools=[tool])
        ))
        run_id = uuid4().hex
        used = set()
        async for message in client.query(
            "Use orders_lookup_order and shipping_status for order A123. "
            "Report the payment total and tracking code from the actual results.", run_id=run_id,
        ):
            if isinstance(message, AssistantMessage):
                used.update(b.name for b in message.content if isinstance(b, ToolUseBlock))
        result = await (await client.get_run(run_id)).result()
        assert used == {"orders_lookup_order", "shipping_status"}, used
        assert "12" in result.text and "COBALT-42" in result.text, result.text
        calls = tool.calls
        followup_id = uuid4().hex
        async for _ in client.query(
            "From our conversation, repeat the payment total and tracking code. Do not call tools.",
            run_id=followup_id,
        ):
            pass
        answer = (await (await client.get_run(followup_id)).result()).text
        assert "12" in answer and "COBALT-42" in answer, answer
        assert tool.calls == calls, "Follow-up unexpectedly repeated the application tool"
        print(f"{profile.harness}: {answer}")


async def main():
    cli = parser(__doc__)
    cli.add_argument("--all", action="store_true", help="Compare all six selectors")
    cli.add_argument("--temporal", action="store_true", help="Use Temporal on localhost:7233")
    args = cli.parse_args()
    cwd, profile = setup(args, "harness-switch", tools=["shipping_status", "orders_lookup_order"])
    profile.mcp_servers = {"orders": {
        "command": sys.executable,
        "args": [str(Path(__file__).with_name("mcp_server.py"))],
        "allowed_tools": ["lookup_order"],
    }}
    if args.temporal:
        profile.temporal = TemporalOptions(checkpoint_path=str(cwd / "checkpoint.sqlite"))
    for harness in available_harnesses() if args.all else [args.harness]:
        # This is the only change to the profile across the comparison.
        selected = profile.model_copy(update={"harness": harness})
        async with asyncio.timeout(180):
            await application(selected, cwd, durable=args.temporal)


if __name__ == "__main__":
    asyncio.run(main())
