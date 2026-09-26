"""Pause a file edit and answer the approval through the public run handle."""

import asyncio

from _common import parser, setup

from liteagents import LiteAgentClient


async def main():
    cli = parser(__doc__)
    cli.add_argument(
        "--approve", action="store_true", help="Approve the demo edit without an interactive prompt"
    )
    args = cli.parse_args()
    cwd, profile = setup(args, "approvals", tools=["read_file", "edit_file"])
    target = cwd / "status.txt"
    target.write_text("status=pending\n")
    profile.harness_options["interrupt_on"] = {"edit_file": True}
    async with LiteAgentClient(profile=profile, cwd=cwd) as client:
        run = await client.start_run(
            "Read status.txt, then use edit_file to replace status=pending with status=approved."
        )
        async for event in run.events():
            if event.kind == "approval_requested":
                print("Requested edit:", event.data["arguments"])
                assert target.read_text() == "status=pending\n"
                allow = (
                    args.approve
                    or (await asyncio.to_thread(input, "Approve this demo edit? [y/N] ")).lower()
                    == "y"
                )
                if not allow:
                    await run.cancel()
                    print("Cancelled; file still contains:", target.read_text().strip())
                    return
                await run.approve(event.data["id"])
        print((await run.result()).text)
        assert target.read_text() == "status=approved\n"
        print("Verified:", target.read_text().strip())


if __name__ == "__main__":
    asyncio.run(main())
