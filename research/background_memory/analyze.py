"""Produce a compact, auditable summary plus compressed complete research traces."""

from __future__ import annotations

import argparse
import gzip
import json
import math
from pathlib import Path


def summary(result):
    calls = result.get("calls", [])
    main = [c for c in calls if c.get("role", "observer" if "luna" in c["model"] else "main") == "main"]
    latencies = sorted(t["seconds"] for t in result["turns"])
    cache = sum(c.get("usage", {}).get("cache_read_input_tokens", 0) for c in main)
    total_input = sum(sum(c.get("usage", {}).get(k, 0) for k in (
        "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")) for c in main)
    return {"label": result["label"], "passed": result["passed"],
            "cost": result["charged_or_reserved"], "peak_main_input": result["main_peak_input_tokens"],
            "main_cache_read_fraction": cache / total_input if total_input else 0,
            "median_turn_seconds": result["median_turn_seconds"],
            "p95_turn_seconds": latencies[max(0, math.ceil(len(latencies) * .95) - 1)] if latencies else None,
            "turns": len(latencies), "main_calls": len(main), "observer_calls": len(calls) - len(main),
            "recovery_calls": sum(c["name"].startswith("memory_") for c in result.get("tool_calls", [])),
            "error": result.get("error"), "checks": result["checks"],
            "action_check": result.get("action_check"), "workflow_check": result.get("workflow_check"),
            "configuration": result.get("configuration"), "source_digest": result.get("source_digest"),
            "research_digest": result.get("research_digest")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", default="research/background_memory/results")
    args = parser.parse_args()
    root = Path(args.results)
    traces = {p.name: json.loads(p.read_text()) for p in sorted(root.glob("v*.json"))
              if not p.name.endswith(".partial.json")}
    rows = [summary(d) for d in traces.values() if "passed" in d]
    (root / "summary.json").write_text(json.dumps(rows, indent=2) + "\n")
    packed = json.dumps(traces, separators=(",", ":")).encode()
    (root / "traces.json.gz").write_bytes(gzip.compress(packed, mtime=0))
    print("| Run | Pass | Cost | Peak input | Cache read | Median / p95 seconds |")
    print("|---|---:|---:|---:|---:|---:|")
    for r in rows:
        print(f"| {r['label']} | {r['passed']} | ${r['cost']:.4f} | {r['peak_main_input']:,} | "
              f"{r['main_cache_read_fraction']:.0%} | {r['median_turn_seconds']:.2f} / {r['p95_turn_seconds']:.2f} |")


if __name__ == "__main__":
    main()
