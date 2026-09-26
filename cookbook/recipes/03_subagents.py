"""Delegate to a named child with its own model and restricted tools."""

import asyncio
import os

from _common import parser, setup

from liteagents import LiteAgentClient, SubagentOptions


async def main():
    args = parser(__doc__).parse_args()
    cwd, profile = setup(args, "subagents", tools=["delegate_auditor"])
    (cwd / "receipt.txt").write_text("Order A123 was paid. The total is USD 12.\n")
    child_model = os.environ.get("LITEAGENTS_SUBAGENT_MODEL")
    profile.subagents = {
        "auditor": SubagentOptions(
            description="Verify orders against receipt.txt; report the evidence.",
            model=child_model or profile.model,
            tools=["read_file"],
        )
    }
    async with LiteAgentClient(profile=profile, cwd=cwd) as client:
        run = await client.start_run(
            "Use delegate_auditor to verify the payment and total for order A123. Relay its findings."
        )
        async for event in run.events():
            if event.kind in ("subagent_started", "subagent_completed"):
                print(event.kind, event.data["agent"])
        result = await run.result()
        assert "12" in result.text, result.text
        print(result.text)


if __name__ == "__main__":
    asyncio.run(main())
