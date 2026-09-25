"""Serial experiment matrix; shares the private spend ledger across all variants."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def matrix(phase):
    common = ["--context", "8000", "--memory", "1000", "--min-observation", "4000"]
    if phase == "explore":
        yield "v4-receipts", "background", "incidents_long", ["--context", "4000", "--memory", "640"]
        for case in ["release_long", "incidents_long", "workflow_long"]:
            yield "v4-control", "full", case, []
        # Matched controls first. Keep the same seed across policy comparisons.
        yield "v4-control", "full,summary", "inventory_long", []
        yield "v4-control", "full,summary", "lookup_long", []
        yield "v4-balanced", "background", "long", common
        for style in ["json", "terse", "delta"]:
            for case in ["inventory_long", "lookup_long"]:
                yield f"v4-{style}", "background", case, [*common, "--style", style]
        yield "v4-batched", "background", "incidents_long", ["--context", "16000", "--memory", "1000", "--min-observation", "8000"]
        yield "v4-stable", "background", "release_long", [*common, "--style", "delta", "--stable-notes"]
        yield "v4-layout", "background", "release_long", [*common, "--style", "delta", "--stable-notes", "--layout", "before_current_request"]
        yield "v4-strict", "background", "release_long", [*common, "--turns", "1", "--min-observation", "1"]
        yield "v4-pause", "background", "release_long", [*common, "--user-pause", "2"]
        yield "v4-luna", "full", "inventory_long", ["--main-model", "luna"]
        yield "v4-luna", "full", "lookup_long", ["--main-model", "luna"]
    elif phase == "pipeline":
        for case in ["release_long", "inventory_long", "workflow_long"]:
            yield "v4-eager", "background", case, [*common, "--schedule", "eager_input"]
        yield "v4-eager-strict", "background", "release_long", [*common, "--schedule", "eager_input", "--turns", "1", "--min-observation", "1"]
    elif phase == "holdout":
        yield "v5-short", "full,background", "short", common
        yield "v5-heldout", "full,background", "long", [*common, "--heldout"]
    elif phase == "scale":
        for case in ["release_long", "lookup_long"]:
            yield "v5-scale", "full,background", case, [*common, "--scale", "3"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-dir", required=True)
    parser.add_argument("--phase", choices=["explore", "pipeline", "holdout", "scale"], required=True)
    parser.add_argument("--seed", type=int, default=901)
    parser.add_argument("--version-prefix", help="Fresh prefix for this sweep, preserving earlier trials")
    parser.add_argument("--recover-context", action="store_true")
    parser.add_argument("--output", default="research/background_memory/results")
    args = parser.parse_args()
    for version, modes, scenario, extra in matrix(args.phase):
        if args.version_prefix:
            version = args.version_prefix + "-" + version.split("-", 1)[1]
        if args.recover_context:
            extra = [*extra, "--recover-context"]
        subprocess.run([sys.executable, str(Path(__file__).with_name("run.py")),
                        "--private-dir", args.private_dir, "--output", args.output,
                        "--version", version, "--modes", modes, "--scenario", scenario,
                        "--seed", str(args.seed), *extra], check=True)


if __name__ == "__main__":
    main()
