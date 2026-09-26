"""Shared setup for the independently runnable recipes in this directory."""

import argparse
import os
from pathlib import Path

from liteagents import ProfileOptions, available_harnesses


def parser(description):
    result = argparse.ArgumentParser(description=description)
    result.add_argument("--harness", choices=available_harnesses(), default="pydantic-ai")
    result.add_argument("--workspace", type=Path)
    return result


def setup(args, recipe, *, tools=None):
    workspace = (args.workspace or Path(".liteagents/recipes") / recipe / args.harness).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    settings = {}
    if endpoint := os.environ.get("LITEAGENTS_API_BASE"):
        for name in ("LITEAGENTS_MODEL", "LITELLM_API_KEY"):
            if not os.environ.get(name):
                raise SystemExit(f"Set {name} for your gateway; see cookbook/recipes/README.md")
        settings = {"api_base": endpoint, "api_key": os.environ["LITELLM_API_KEY"]}
    profile = ProfileOptions(
        harness=args.harness,
        model=os.environ.get("LITEAGENTS_MODEL", "openai/gpt-5.4-mini"),
        model_kwargs=settings,
        tools=tools,
        max_turns=10,
        system_prompt="Use the requested tools and report their actual results. Be concise.",
    )
    return workspace, profile
