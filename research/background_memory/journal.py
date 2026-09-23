"""Append-only research provenance, including failed and interrupted trials."""

from __future__ import annotations

import hashlib
import json
import os
import statistics
from datetime import datetime, timezone
from pathlib import Path


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def provenance():
    def digest(paths):
        return hashlib.sha256(b"".join(p.read_bytes() for p in sorted(paths))).hexdigest()
    return {"source_digest": digest(Path("src/liteagents").rglob("*.py")),
            "research_digest": digest(Path("research/background_memory").glob("*.py"))}


def append_event(output, event, result, artifact, *, reconstructed=False):
    record = {"recorded_at": utc_now(), "event": event, "label": result["label"],
              "artifact": artifact.name, "reconstructed": reconstructed,
              **{key: result.get(key) for key in (
                  "started_at", "finished_at", "configuration", "source_digest", "research_digest",
                  "outcome", "passed", "charged_or_reserved", "error", "abort_reason")}}
    with (output / "events.jsonl").open("a") as stream:
        stream.write(json.dumps(record, separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def finalize(result, calls, *, aborted=False):
    result["calls"] = calls
    result["known_cost"] = sum(c.get("estimated_cost", 0) for c in calls)
    result["charged_or_reserved"] = sum(c["charged_or_reserved"] for c in calls)
    result["main_peak_input_tokens"] = max((
        sum(c.get("usage", {}).get(k, 0) for k in (
            "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        for c in calls if c.get("role", "observer" if "luna" in c["model"] else "main") == "main"), default=0)
    latencies = [t["seconds"] for t in result["turns"]]
    result["median_turn_seconds"] = statistics.median(latencies) if latencies else None
    passed = bool(result["checks"]) and all(c["passed"] for c in result["checks"]) and not result.get("error")
    if "action_check" in result:
        passed &= result["action_check"]["all_reviewed"] and (
            result["action_check"]["writes"] == result["action_check"]["expected_writes"])
    if "workflow_check" in result:
        passed &= all(result["workflow_check"].values())
    result["passed"] = None if aborted else bool(passed)
    result["outcome"] = "research-aborted" if aborted else "passed" if passed else "failed"
    result["finished_at"] = utc_now()
    return result
