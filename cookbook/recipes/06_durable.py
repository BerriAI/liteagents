"""Submit, attach, cancel and recover a Temporal run from separate processes."""

import asyncio
import os
from typing import ClassVar

from _common import parser, setup

from liteagents import LiteAgentClient, TemporalOptions, Tool, operation_id
from liteagents.temporal import LiteAgentWorker


class Receipt(Tool):
    name = "save_receipt"
    description = "Save the receipt once and return its tracking code."
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def __init__(self, cwd):
        self.cwd = cwd

    async def execute(self, input):
        key = operation_id()
        assert key
        directory = self.cwd / "receipts"
        directory.mkdir(exist_ok=True)
        path = directory / key
        try:
            with path.open("x") as stream:
                stream.write("receipt-verified")
            print("Receipt saved once", flush=True)
        except FileExistsError:
            print("Reused the receipt idempotency key", flush=True)
        return "Receipt saved: receipt-verified"


class SlowCheck(Tool):
    name = "slow_check"
    description = "Check the saved receipt and report its verification result."
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def __init__(self, delay):
        self.delay = delay

    async def execute(self, input):
        print(
            "Slow check started; this is the point to kill the worker in the crash demo.",
            flush=True,
        )
        await asyncio.sleep(self.delay)
        print("Slow check completed", flush=True)
        return "Receipt verification passed: receipt-verified"


async def main():
    cli = parser(__doc__)
    cli.add_argument("action", choices=["worker", "start", "result", "events", "cancel", "purge"])
    cli.add_argument("run_id", nargs="?")
    cli.add_argument("--delay", type=int, default=30)
    cli.add_argument("--days", type=int, default=30)
    args = cli.parse_args()
    cwd, profile = setup(args, "durable", tools=["save_receipt", "slow_check"])
    profile.temporal = TemporalOptions(
        profile_id=f"recipes-v2a2-{args.harness}-{args.delay}",
        checkpoint_path=str(cwd / "checkpoints.sqlite"),
        heartbeat_timeout_seconds=3,
        activity_timeout_seconds=180,
    )
    tools = [Receipt(cwd), SlowCheck(args.delay)]
    worker = LiteAgentWorker(profile=profile, tools=tools, cwd=cwd)
    if args.action == "worker":
        print(f"Worker PID: {os.getpid()}; workspace: {cwd}", flush=True)
        await worker.run()
        return
    if args.action == "purge":
        print("Expired runs removed:", await worker.purge(older_than_days=args.days))
        return
    if not args.run_id:
        cli.error("run_id is required for this action")
    async with LiteAgentClient(profile=profile, cwd=cwd) as client:
        if args.action == "start":
            run = await client.start_run(
                "Call save_receipt once, then slow_check once. Report their results.",
                run_id=args.run_id,
            )
            print("Submitted:", run.run_id)
            return
        run = await client.get_run(args.run_id)
        if args.action == "result":
            result = await run.result()
            assert "receipt-verified" in result.text, result.text
            print(result.text)
        elif args.action == "events":
            async for event in run.events():
                print(event.cursor, event.kind, event.data)
            await run.result()
        else:
            await run.cancel()
            print("Cancellation requested")


if __name__ == "__main__":
    asyncio.run(main())
