"""Inject two transient tool failures and watch the SDK retry the operation."""

import asyncio
from typing import ClassVar

from _common import parser, setup

from liteagents import LiteAgentClient, RecoveryOptions, Tool, operation_id


class UnstableLookup(Tool):
    name = "unstable_lookup"
    description = "Return the confirmed total for order A123."
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def __init__(self):
        self.attempts = 0
        self.keys = []

    async def execute(self, input):
        self.attempts += 1
        self.keys.append(operation_id())
        print("Tool attempt", self.attempts)
        if self.attempts < 3:
            raise TimeoutError("Simulated temporary failure")
        return "Confirmed total: USD 12"


async def main():
    args = parser(__doc__).parse_args()
    cwd, profile = setup(args, "retries", tools=["unstable_lookup"])
    profile.recovery = RecoveryOptions()
    tool = UnstableLookup()
    async with LiteAgentClient(profile=profile, cwd=cwd, tools=[tool]) as client:
        run = await client.start_run("Call unstable_lookup once and report its result.")
        print((await run.result()).text)
    assert tool.attempts == 3 and len(set(tool.keys)) == 1
    print("Verified: three attempts used the same application idempotency key.")


if __name__ == "__main__":
    asyncio.run(main())
