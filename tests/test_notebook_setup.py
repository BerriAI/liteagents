"""Notebook credentials work independently of a checkout or a running gateway."""

# Execute only the committed notebook cells under test.
# ruff: noqa: S102

import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import nbformat
import pytest

from tests.test_notebooks import NOTEBOOKS, ROOT

KEYS = {
    "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY", "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY", "mistral": "MISTRAL_API_KEY",
    "together_ai": "TOGETHERAI_API_KEY", "deepseek": "DEEPSEEK_API_KEY",
    "xai": "XAI_API_KEY", "azure": "AZURE_API_KEY",
}


def credentials(path):
    notebook = nbformat.read(path, as_version=4)
    return next(c.source for c in notebook.cells if "credentials" in c.metadata.get("tags", []))


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.stem)
@pytest.mark.parametrize("provider,key", KEYS.items())
def test_provider_key_from_environment(path, provider, key, monkeypatch):
    prompt = Mock(side_effect=AssertionError("An existing provider key must not prompt"))
    monkeypatch.setattr("getpass.getpass", prompt)
    monkeypatch.setattr(os, "environ", {key: "synthetic-key"})
    namespace = {"os": os, "MODEL": f"{provider}/example", "API_BASE": ""}
    exec(credentials(path), namespace)
    assert namespace["API_KEY"] == "synthetic-key"
    assert namespace.get("MODEL_KWARGS", {}) == {}


@pytest.mark.parametrize("provider", ["bedrock", "vertex_ai", "ollama_chat"])
def test_cloud_or_local_provider_does_not_demand_an_api_key(provider, monkeypatch):
    monkeypatch.setattr("getpass.getpass", Mock(side_effect=AssertionError("Unexpected key prompt")))
    monkeypatch.setattr(os, "environ", {})
    namespace = {"os": os, "MODEL": f"{provider}/example", "API_BASE": ""}
    exec(credentials(ROOT / "cookbook/recipes/00_agent.ipynb"), namespace)
    assert namespace["API_KEY"] is None
    assert namespace.get("MODEL_KWARGS", {}) == {}


def test_gateway_uses_its_own_key_and_keeps_the_alias(monkeypatch):
    monkeypatch.setattr(os, "environ", {"LITELLM_API_KEY": "gateway-synthetic"})
    namespace = {"os": os, "MODEL": "anthropic/team-alias", "API_BASE": "https://gateway.example/v1"}
    exec(credentials(ROOT / "cookbook/recipes/01_quickstart.ipynb"), namespace)
    assert namespace["MODEL"] == "anthropic/team-alias"
    assert namespace["MODEL_KWARGS"] == {
        "api_base": "https://gateway.example/v1", "api_key": "gateway-synthetic",
    }


@pytest.mark.parametrize("outcome", ["secret", "missing", "denied"])
def test_colab_secrets_and_hidden_prompt(outcome, monkeypatch):
    class SecretNotFoundError(Exception):
        pass

    class NotebookAccessError(Exception):
        pass

    def get(name):
        assert name == "OPENROUTER_API_KEY"
        if outcome == "missing":
            raise SecretNotFoundError
        if outcome == "denied":
            raise NotebookAccessError
        return "colab-synthetic"

    google = ModuleType("google")
    colab = ModuleType("google.colab")
    colab.userdata = SimpleNamespace(  # type: ignore[attr-defined]
        get=get, SecretNotFoundError=SecretNotFoundError, NotebookAccessError=NotebookAccessError,
    )
    google.colab = colab  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.colab", colab)
    monkeypatch.setattr(os, "environ", {})
    prompt = Mock(return_value="prompt-synthetic")
    monkeypatch.setattr("getpass.getpass", prompt)
    namespace = {"os": os, "MODEL": "openrouter/anthropic/example", "API_BASE": ""}
    exec(credentials(ROOT / "cookbook/recipes/00_agent.ipynb"), namespace)
    expected = "colab-synthetic" if outcome == "secret" else "prompt-synthetic"
    assert namespace["API_KEY"] == os.environ["OPENROUTER_API_KEY"] == expected
    assert prompt.call_count == (outcome != "secret")
