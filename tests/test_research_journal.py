"""Research evidence must retain aborted costs without claiming a successful run."""

import json
import runpy
from pathlib import Path

import httpx
import pytest


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


async def test_gateway_bypasses_response_reuse_and_reserves_budget_before_sending(tmp_path, monkeypatch):
    boundary = runpy.run_path(str(Path(__file__).parents[1] / "research/background_memory/gateway.py"))
    (tmp_path / "gateway.key").write_text("test-placeholder")
    gateway = boundary["Gateway"](tmp_path, budget=0.01)
    sent = []

    async def post(url, **kwargs):
        sent.append(kwargs)
        return httpx.Response(200, json={"id": "test-response", "content": [],
            "usage": {"input_tokens": 10, "output_tokens": 2}},
            headers={"x-litellm-response-cost": "0.001"})

    monkeypatch.setattr(gateway.client, "post", post)
    try:
        await gateway(model=boundary["MEMORY"], messages=[{"role": "user", "content": "hello"}], max_tokens=10)
        assert sent[0]["json"]["cache"] == {"no-cache": True, "no-store": True}
        assert sent[0]["headers"] == {"Cache-Control": "no-cache, no-store"}
        assert gateway.ledger[-1]["charged_or_reserved"] == 0.001
        assert gateway.ledger[-1]["gateway_response_cache"] == "disabled"
        assert len(gateway.ledger[-1]["response_id_sha256"]) == 64
        with pytest.raises(boundary["BudgetExceeded"]):
            await gateway(model=boundary["MAIN"], messages=[{"role": "user", "content": "x" * 1000}], max_tokens=100)
        assert len(sent) == 1
        assert json.loads((tmp_path / "ledger.json").read_text()) == gateway.ledger
    finally:
        await gateway.close()
