"""Shared setup for the independently runnable recipes in this directory."""

import argparse
import os
from pathlib import Path

from liteagents import ProfileOptions, available_harnesses


def parser(description):
    result = argparse.ArgumentParser(description=description)
    result.add_argument("--harness", choices=available_harnesses(), default="deepagents")
    result.add_argument("--workspace", type=Path)
    return result


def setup(args, recipe, *, tools=None):
    workspace = (args.workspace or Path(".liteagents/recipes") / recipe / args.harness).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    model_env = "LITEAGENTS_CLAUDE_MODEL" if args.harness == "claude-sdk" else "LITEAGENTS_MODEL"
    for name in ("LITEAGENTS_API_BASE", "LITELLM_API_KEY", model_env):
        if not os.environ.get(name):
            raise SystemExit(f"Set {name}; see cookbook/recipes/README.md")
    profile = ProfileOptions(
        harness=args.harness,
        model="litellm_proxy/" + os.environ[model_env],
        model_kwargs={
            "api_base": os.environ["LITEAGENTS_API_BASE"],
            "api_key": os.environ["LITELLM_API_KEY"],
        },
        tools=tools or [],
        recovery={},
        max_turns=10,
        system_prompt="Use the requested tools and report their actual results. Be concise.",
    )
    return workspace, profile
