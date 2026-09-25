"""Actual CLI worker process for managed operation crash tests."""

import asyncio
import sys
from pathlib import Path

from liteagents import ProfileOptions, TemporalOptions
from liteagents.temporal import LiteAgentWorker
from tests.operation_fixture import FixtureTool


async def main():
    cwd, version, harness, url = sys.argv[1:5]
    cwd = Path(cwd)
    import json
    import os

    from liteagents.runtime.control import RunControl

    original = RunControl.call

    async def traced(self, kind, key, arguments, function, **kwargs):
        if kind == "model":
            with (cwd / f"requests-{os.getpid()}.jsonl").open("a") as log:
                log.write(json.dumps({"key": key, "arguments": arguments}) + "\n")
        return await original(self, kind, key, arguments, function, **kwargs)

    RunControl.call = traced
    profile = ProfileOptions(
        harness=harness,
        model="litellm_proxy/scripted",
        model_kwargs={"api_base": url, "api_key": "test"},
        tools=["lookup", "slow"],
        recovery={"retries": {"max_attempts": 3}},
        harness_options={"timeout_seconds": 60},
        system_prompt="Use lookup, then slow, then report the result. Use only those tools.",
        temporal=TemporalOptions(
            profile_id=version,
            checkpoint_path=str(cwd / "checkpoint.sqlite"),
            heartbeat_timeout_seconds=3,
            activity_timeout_seconds=90,
        ),
    )
    tools = [FixtureTool(name, cwd / "calls.txt") for name in ("lookup", "slow")]
    mode = sys.argv[5] if len(sys.argv) > 5 else "tool"
    if mode == "approval":
        profile.harness_options["interrupt_on"] = {"slow": True}
    if mode == "child":
        from liteagents.profiles import SubagentOptions

        profile.tools = ["read_file"]
        profile.features.subagents = True
        profile.subagents = {
            "auditor": SubagentOptions(description="Audit order", tools=["lookup", "slow"])
        }
    await LiteAgentWorker(profile=profile, tools=tools, cwd=cwd).run()


if __name__ == "__main__":
    asyncio.run(main())
