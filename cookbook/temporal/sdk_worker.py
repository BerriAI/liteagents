"""Run the public LiteAgents worker against an existing Temporal service."""

import argparse
import asyncio

from liteagents import ProfileOptions
from liteagents.temporal import LiteAgentWorker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile")
    parser.add_argument("--cwd", default=".")
    args = parser.parse_args()
    profile = ProfileOptions.from_yaml(args.profile)
    asyncio.run(LiteAgentWorker(profile=profile, cwd=args.cwd).run())


if __name__ == "__main__":
    main()
