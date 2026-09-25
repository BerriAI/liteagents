"""Produce a compact, auditable summary plus compressed complete research traces."""

from __future__ import annotations

import argparse
import gzip
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def summary(result):
    calls = result.get("calls", [])
    main = [c for c in calls if c.get("role", "observer" if "luna" in c["model"] else "main") == "main"]
    latencies = sorted(t["seconds"] for t in result["turns"])
    cache = sum(c.get("usage", {}).get("cache_read_input_tokens", 0) for c in main)
    total_input = sum(sum(c.get("usage", {}).get(k, 0) for k in (
        "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")) for c in main)
    return {"label": result["label"], "passed": result["passed"],
            "outcome": result.get("outcome", "passed" if result["passed"] else "failed"),
            "context_recoveries": result.get("context_recoveries", []),
            "cost": result["charged_or_reserved"], "peak_main_input": result["main_peak_input_tokens"],
            "main_cache_read_fraction": cache / total_input if total_input else 0,
            "median_turn_seconds": result["median_turn_seconds"],
            "p95_turn_seconds": latencies[max(0, math.ceil(len(latencies) * .95) - 1)] if latencies else None,
            "turns": len(latencies), "main_calls": len(main), "observer_calls": len(calls) - len(main),
            "recovery_calls": sum(c["name"].startswith("memory_") for c in result.get("tool_calls", [])),
            "error": result.get("error"), "checks": result["checks"],
            "observer_failures": result.get("failures", []),
            "interrupted_turns": result.get("interruptions", []),
            "action_check": result.get("action_check"), "workflow_check": result.get("workflow_check"),
            "configuration": result.get("configuration"), "source_digest": result.get("source_digest"),
            "research_digest": result.get("research_digest")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", default="research/background_memory/results")
    parser.add_argument("--ledger", type=Path, help="Optional private ledger; publishes aggregates only")
    args = parser.parse_args()
    root = Path(args.results)
    traces = {p.name: json.loads(p.read_text()) for p in sorted(root.glob("v*.json"))
              if not p.name.endswith(".partial.json")}
    rows = [summary(d) for d in traces.values() if "passed" in d]
    (root / "summary.json").write_text(json.dumps(rows, indent=2) + "\n")
    packed = json.dumps(traces, separators=(",", ":")).encode()
    (root / "traces.json.gz").write_bytes(gzip.compress(packed, mtime=0))
    if args.ledger:
        ledger = json.loads(args.ledger.read_text())
        ids = [c["id"] for c in ledger]
        if len(set(ids)) != len(ids):
            raise ValueError("Ledger request IDs must be unique")
        traced = {c.get("id") for result in traces.values() for c in result.get("calls", [])}
        def totals(calls):
            return {"requests": len(calls), "statuses": dict(Counter(c["status"] for c in calls)),
                    "charged_or_reserved_usd": sum(c["charged_or_reserved"] for c in calls),
                    "completed_reported_or_estimated_usd": sum(
                        c["charged_or_reserved"] for c in calls if c["status"] == "completed"),
                    "unresolved_reserved_usd": sum(
                        c["charged_or_reserved"] for c in calls if c["status"] != "completed")}
        accounting = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "authorized_ceiling_usd": 200, "harness_ceiling_usd": 190,
            "basis": "Gateway reported charges or usage estimates, with full reservations for ambiguous calls; not an independently reconciled invoice.",
            "all_requests": totals(ledger),
            "by_model": {model: totals([c for c in ledger if c["model"] == model])
                         for model in sorted({c["model"] for c in ledger})},
            "requests_in_result_artifacts": totals([c for c in ledger if c["id"] in traced]),
            "additional_diagnostics": totals([c for c in ledger if c["id"] not in traced]),
        }
        (root / "accounting.json").write_text(json.dumps(accounting, indent=2) + "\n")
    print("| Run | Pass | Cost | Peak input | Cache read | Median / p95 seconds |")
    print("|---|---:|---:|---:|---:|---:|")
    for r in rows:
        median = f"{r['median_turn_seconds']:.2f}" if r["median_turn_seconds"] is not None else "—"
        p95 = f"{r['p95_turn_seconds']:.2f}" if r["p95_turn_seconds"] is not None else "—"
        print(f"| {r['label']} | {r['passed']} | ${r['cost']:.4f} | {r['peak_main_input']:,} | "
              f"{r['main_cache_read_fraction']:.0%} | {median} / {p95} |")


if __name__ == "__main__":
    main()
