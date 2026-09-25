import json

import pytest
from pydantic import ValidationError

from liteagents import ConfigurationError, LiteAgentOptions, ProfileOptions, available_harnesses


def test_all_six_harnesses_are_named():
    assert set(available_harnesses()) == {
        "deepagents",
        "pydantic-ai",
        "claude-sdk",
        "codex",
        "opencode-v1",
        "opencode-v2",
    }


def test_yaml_and_json_expand_nested_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_LITEAGENTS_KEY", "private-value")
    values = {
        "harness": "deepagents",
        "model": "litellm_proxy/test",
        "model_kwargs": {"api_key": "${TEST_LITEAGENTS_KEY}"},
    }
    json_path = tmp_path / "agent.json"
    json_path.write_text(json.dumps(values))
    yaml_path = tmp_path / "agent.yaml"
    yaml_path.write_text(
        "harness: deepagents\nmodel: litellm_proxy/test\nmodel_kwargs:\n  api_key: ${TEST_LITEAGENTS_KEY}\n"
    )
    first = ProfileOptions.from_json(json_path)
    assert first == ProfileOptions.from_yaml(yaml_path)
    assert first.model_kwargs["api_key"] == "private-value"
    assert "private-value" not in repr(first)
    assert "private-value" not in first.identity()


def test_missing_environment_fails_without_echoing_profile(tmp_path, monkeypatch):
    monkeypatch.delenv("LITEAGENTS_MISSING", raising=False)
    path = tmp_path / "agent.json"
    path.write_text('{"harness":"deepagents","model":"${LITEAGENTS_MISSING}"}')
    with pytest.raises(ConfigurationError, match="LITEAGENTS_MISSING"):
        ProfileOptions.from_json(path)


@pytest.mark.parametrize(
    "change",
    [
        {"harness": "unknown"},
        {"model": ""},
        {"max_turns": 0},
        {"typo": True},
        {"tools": ["x", "x"]},
        {"tools": [""]},
        {"model_kwargs": {"messages": []}},
        {"features": {"streamng": True}},
        {"recovery": {"retries": {"max_attempts": 0}}},
    ],
)
def test_invalid_profiles_rejected(change):
    with pytest.raises(ValidationError):
        ProfileOptions(**({"harness": "deepagents", "model": "openai/test"} | change))


def test_profile_and_legacy_settings_cannot_mix():
    profile = ProfileOptions(harness="deepagents", model="openai/test")
    with pytest.raises(TypeError, match="model"):
        LiteAgentOptions(profile=profile, model="another")


def test_native_objects_require_an_explicit_temporal_version():
    profile = ProfileOptions(
        harness="deepagents", model="test", harness_options={"model_instance": object()}
    )
    with pytest.raises(ConfigurationError, match="profile_id"):
        profile.identity()
    profile.temporal = {"profile_id": "scripted-v1"}
    assert profile.identity() == "scripted-v1"
