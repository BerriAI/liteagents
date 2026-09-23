"""Research evidence must retain aborted costs without claiming a successful run."""

import json
import runpy
from pathlib import Path


def test_aborted_trial_keeps_reservations_and_prior_log_entries(tmp_path):
    journal = runpy.run_path(str(Path(__file__).parents[1] / "research/background_memory/journal.py"))
    result = {"label": "trial/background/case", "turns": [{"seconds": 2}],
              "checks": [{"passed": True}]}
    calls = [{"model": "astra", "role": "main", "charged_or_reserved": 1.25,
              "status": "cancelled"}]
    artifact = tmp_path / "trial.json"
    journal["append_event"](tmp_path, "started", result, artifact)
    original = (tmp_path / "events.jsonl").read_bytes()
    journal["finalize"](result, calls, aborted=True)
    journal["append_event"](tmp_path, "aborted", result, artifact)
    assert (tmp_path / "events.jsonl").read_bytes().startswith(original)
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert [e["event"] for e in events] == ["started", "aborted"]
    assert events[-1]["outcome"] == "research-aborted"
    assert events[-1]["passed"] is None
    assert events[-1]["charged_or_reserved"] == 1.25
    assert result["calls"] == calls
