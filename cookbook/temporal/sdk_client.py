"""Submit, inspect, or attach to a public SDK run; never resubmit on attach."""

import argparse
import asyncio

from liteagents import LiteAgentClient, ProfileOptions


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile")
    parser.add_argument("action", choices=["start", "result", "status"])
    parser.add_argument("run_id")
    parser.add_argument("--prompt")
    args = parser.parse_args()
    if args.action == "start" and not args.prompt:
        parser.error("start requires --prompt")
    profile = ProfileOptions.from_yaml(args.profile)
    async with LiteAgentClient(profile=profile) as client:
        if args.action == "start":
            print((await client.start_run(args.prompt, run_id=args.run_id)).run_id)
        else:
            run = await client.get_run(args.run_id)
            print((await run.result()).text if args.action == "result" else await run.status())


if __name__ == "__main__":
    asyncio.run(main())
