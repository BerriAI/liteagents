"""Load an agent profile from JSON or YAML and run it without Temporal."""

import argparse
import asyncio
from pathlib import Path

from liteagents import LiteAgentClient, LiteAgentOptions, ProfileOptions, available_harnesses


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "profile", nargs="?", type=Path,
        default=Path(__file__).with_name("profiles") / "agent.yaml",
    )
    parser.add_argument("--harness", choices=available_harnesses())
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--prompt", default="Reply with exactly READY.")
    args = parser.parse_args()
    if args.profile.suffix.lower() == ".json":
        profile = ProfileOptions.from_json(args.profile)
    elif args.profile.suffix.lower() in (".yaml", ".yml"):
        profile = ProfileOptions.from_yaml(args.profile)
    else:
        parser.error("Profile must be a .json, .yaml, or .yml file")
    if profile.temporal is not None:
        parser.error("This recipe runs directly; use the durable cookbook for Temporal profiles")
    if args.harness is not None:
        profile.harness = args.harness
    cwd = (args.workspace or Path(".liteagents/recipes/profile-files") / profile.harness).resolve()
    cwd.mkdir(parents=True, exist_ok=True)
    async with LiteAgentClient(options=LiteAgentOptions(profile=profile, cwd=cwd)) as client:
        run = await client.start_run(args.prompt)
        print((await run.result()).text)


if __name__ == "__main__":
    asyncio.run(main())
