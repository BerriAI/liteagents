import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from cookbook.compare_harnesses import compare as runner


async def test_comparison_copies_workspaces_reports_failures_and_redacts(monkeypatch, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    (source / "calculator.py").write_text("broken\n")
    (source / ".env").write_text("secret data")
    (source / "escape").symlink_to(tmp_path)
    observed = []

    class Client:
        def __init__(self, *, profile, cwd):
            self.cwd = Path(cwd)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def query(self, prompt, run_id=None):
            assert (self.cwd / "calculator.py").read_text() == "broken\n"
            assert not (self.cwd / ".env").exists()
            assert not (self.cwd / "escape").exists()
            observed.append(self.cwd)
            if len(observed) == 2:
                raise RuntimeError("failure with fixture-secret-token")
            (self.cwd / "calculator.py").write_text("fixed\n")
            yield None

        async def get_run(self, id):
            return self

        async def result(self):
            return SimpleNamespace(text="answer fixture-secret-token", usage={})

    monkeypatch.setattr(runner, "LiteAgentClient", Client)
    profiles = []
    for h in ["deepagents", "pydantic-ai"]:
        path = tmp_path / (h + ".yaml")
        path.write_text(
            yaml.safe_dump(
                {"harness": h, "model": "test", "model_kwargs": {"api_key": "fixture-secret-token"}}
            )
        )
        profiles.append(path)
    output = tmp_path / "report"
    results = await runner.compare(
        profiles,
        source,
        output,
        "fix",
        [
            sys.executable,
            "-c",
            "from pathlib import Path; assert Path('calculator.py').read_text()=='fixed\\n'",
        ],
    )
    assert (source / "calculator.py").read_text() == "broken\n"
    assert len(set(observed)) == 2
    assert results[0]["tests"]["exit_code"] == 0
    assert results[1]["status"] == "failed"
    assert results[1]["tests"]["exit_code"] != 0
    assert results[0]["usage"] is None
    assert "fixture-secret-token" not in (output / "results.json").read_text()
    assert "-broken" in (output / "01-deepagents/changes.diff").read_text()
    assert json.loads((output / "results.json").read_text()) == results
    with pytest.raises(FileExistsError):
        await runner.compare(profiles, source, output, "fix", [sys.executable, "-V"])
    with pytest.raises(ValueError, match="outside"):
        await runner.compare(profiles, source, source / "reports", "fix", [sys.executable, "-V"])
