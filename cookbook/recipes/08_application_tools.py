"""Register a Python tool and keep the same application code across harnesses."""

import asyncio
from typing import ClassVar

from _common import parser, setup

from liteagents import AssistantMessage, LiteAgentClient, LiteAgentOptions, Tool, ToolUseBlock


class LookupOrder(Tool):
    name = "lookup_order"
    description = "Return the payment status of an order."
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"],
        "additionalProperties": False,
    }

    def __init__(self):
        self.calls = []

    async def execute(self, input):
        self.calls.append(input)
        return f"Order {input['order_id']}: paid, total USD 12"


async def main():
    args = parser(__doc__).parse_args()
    cwd, profile = setup(args, "application-tools", tools=["lookup_order"])
    tool = LookupOrder()
    async with LiteAgentClient(
        options=LiteAgentOptions(profile=profile, cwd=cwd, tools=[tool])
    ) as client:
        print("Custom tools supported:", client.capabilities.custom_tools)
        async for message in client.query(
            "Call lookup_order for A123 and report its payment status and total.", run_id="order"
        ):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, ToolUseBlock):
                        assert block.name == "lookup_order", block.name
                        print("Tool:", block.name, block.input)
        result = await (await client.get_run("order")).result()
        assert {"order_id": "A123"} in tool.calls, tool.calls
        assert "12" in result.text and "paid" in result.text.lower(), result.text
        print(result.text)


if __name__ == "__main__":
    asyncio.run(main())
