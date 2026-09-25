"""Run a simple agent with no tools, Temporal service, or database setup."""

import asyncio

from _common import parser, setup

from liteagents import LiteAgentClient, LiteAgentOptions


async def main():
    args = parser(__doc__).parse_args()
    cwd, profile = setup(args, "agent", tools=[])
    async with LiteAgentClient(options=LiteAgentOptions(profile=profile, cwd=cwd)) as client:
        run = await client.start_run("Reply with exactly READY.")
        result = await run.result()
        print(result.text)
        assert "READY" in result.text, result.text


if __name__ == "__main__":
    asyncio.run(main())
