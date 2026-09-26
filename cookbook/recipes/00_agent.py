"""Run a simple agent with no tools, Temporal service, or database setup."""

import asyncio

from _common import parser, setup

from liteagents import run


async def main():
    args = parser(__doc__).parse_args()
    cwd, profile = setup(args, "agent", tools=[])
    result = await run("Reply with exactly READY.", profile=profile, cwd=cwd)
    print(result.text)
    assert "READY" in result.text, result.text


if __name__ == "__main__":
    asyncio.run(main())
