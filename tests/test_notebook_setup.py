"""Each notebook has a standalone, hidden API-key prompt that is safe to rerun."""

# Execute only the committed notebook cells under test.
# ruff: noqa: S102

import os
from unittest.mock import Mock

import nbformat
import pytest

from tests.test_notebooks import NOTEBOOKS


def credentials(path):
    notebook = nbformat.read(path, as_version=4)
    return next(c.source for c in notebook.cells if "credentials" in c.metadata.get("tags", []))


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.stem)
@pytest.mark.parametrize("existing", [True, False])
def test_key_prompt_and_rerun(path, existing, monkeypatch, capsys):
    monkeypatch.setattr(os, "environ", {"OPENAI_API_KEY": "hidden-key"} if existing else {})
    prompt = Mock(return_value="hidden-key")
    monkeypatch.setattr("getpass.getpass", prompt)
    source = credentials(path)
    exec(source, {})
    exec(source, {})
    assert os.environ["OPENAI_API_KEY"] == "hidden-key"
    assert prompt.call_count == (0 if existing else 1)
    assert "hidden-key" not in capsys.readouterr().out


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.stem)
@pytest.mark.parametrize("key", ["", "   "])
def test_empty_key_explains_next_step(path, key, monkeypatch):
    monkeypatch.setattr(os, "environ", {})
    monkeypatch.setattr("getpass.getpass", Mock(return_value=key))
    with pytest.raises(ValueError, match="Run this cell again"):
        exec(credentials(path), {})
    monkeypatch.setattr("getpass.getpass", Mock(return_value="corrected-key"))
    exec(credentials(path), {})
    assert os.environ["OPENAI_API_KEY"] == "corrected-key"
