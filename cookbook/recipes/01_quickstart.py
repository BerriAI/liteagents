"""Read a file, stream the answer, and continue the same conversation."""

import asyncio

from _common import parser, setup

from liteagents import LiteAgentClient, LiteAgentOptions, TextDelta


async def main():
    args = parser(__doc__).parse_args()
    cwd, profile = setup(args, "quickstart", tools=["read_file"])
    (cwd / "facts.txt").write_text("The project verification code is COBALT-42.\n")
    profile.features.streaming = True
    async with LiteAgentClient(options=LiteAgentOptions(profile=profile, cwd=cwd)) as client:
        async for event in client.query(
            "Read facts.txt and report the verification code.", run_id="intro"
        ):
            if isinstance(event, TextDelta):
                print(event.text, end="", flush=True)
        result = await (await client.get_run("intro")).result()
        assert "COBALT-42" in result.text, result.text
        print("\nCompleted:", result.text)
        run = await client.start_run(
            "Repeat the code from our conversation without calling a tool."
        )
        print("Follow-up:", (await run.result()).text)


if __name__ == "__main__":
    asyncio.run(main())
