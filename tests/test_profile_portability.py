import pytest
from pydantic import ValidationError

from liteagents import ConfigurationError, LiteAgentClient, LiteAgentOptions, ProfileOptions
from liteagents.harnesses import available_harnesses, get_capabilities
from liteagents.runtime.tooling import select_tools
from tests.test_portable_contract import Lookup


@pytest.mark.parametrize("harness", available_harnesses())
def test_tool_selection_survives_profile_serialization(harness, tmp_path):
    tool = Lookup()
    profile = ProfileOptions(harness=harness, model="openai/test")
    for selection, expected in ((None, [tool]), ([], []), (["lookup"], [tool])):
        profile.tools = selection
        restored = ProfileOptions.model_validate_json(profile.model_dump_json())
        assert restored.tools == selection
        assert select_tools(restored, [tool], tmp_path) == expected
    profile.tools = ["missing"]
    with pytest.raises(ConfigurationError, match="no registered implementation"):
        select_tools(profile, [tool], tmp_path)


@pytest.mark.parametrize("harness", available_harnesses())
def test_capabilities_follow_profile_and_application_tools(harness):
    profile = ProfileOptions(harness=harness, model="litellm_proxy/test")
    assert get_capabilities(profile).execution_mode == "native"
    for change in ({"tools": []}, {"mcp_servers": {"a": {"command": "python"}}},
                   {"recovery": {}}, {"temporal": {}}):
        configured = ProfileOptions.model_validate({**profile.model_dump(), **change})
        capabilities = get_capabilities(configured)
        assert capabilities.custom_tools
        assert capabilities.execution_mode == (
            "native" if harness in ("deepagents", "pydantic-ai") else "managed"
        )
    assert get_capabilities(profile, tools=[Lookup()]).custom_tools
    if harness in ("codex", "opencode-v1", "opencode-v2"):
        assert not get_capabilities(harness).custom_tools


def test_mcp_aliases_are_canonical_without_mutating_caller():
    server = {"url": "https://example.test/mcp", "http_headers": {"X-Test": "value"},
              "enabled_tools": ["read"]}
    profile = ProfileOptions(harness="codex", model="openai/test", mcp_servers={"a": server})
    assert profile.mcp_servers["a"] == {
        "url": server["url"], "headers": {"X-Test": "value"}, "allowed_tools": ["read"]
    }
    assert "http_headers" in server


@pytest.mark.parametrize("config", [
    {"url": "https://example.test", "allowed_tools": "read"},
    {"url": "https://example.test", "allowed_tools": ["read", "read"]},
    {"url": "https://example.test", "headers": "secret"},
    {"url": "https://example.test", "transport": "unknown"},
    {"url": "https://example.test", "headers": {}, "http_headers": {"X-Test": "x"}},
    {"command": "python", "headers": {}},
    {"command": "python", "env": {"X": 1}},
    {"url": "https://example.test", "args": []},
])
def test_mcp_invalid_settings_fail_at_profile_creation(config):
    with pytest.raises(ValidationError):
        ProfileOptions(harness="deepagents", model="openai/test", mcp_servers={"a": config})


@pytest.mark.parametrize("harness,module", [("claude-sdk", "claude_agent_sdk"),
                                           ("codex", "openai_codex")])
def test_managed_model_settings_fail_before_mcp_or_process_start(harness, module, tmp_path):
    pytest.importorskip(module)
    profile = ProfileOptions(
        harness=harness, model="litellm_proxy/test", tools=["lookup"],
        model_kwargs={"api_base": "http://127.0.0.1:1/v1", "temperature": 0},
        mcp_servers={"a": {"command": "this-command-must-never-start"}},
    )
    with pytest.raises(ConfigurationError, match="temperature"):
        LiteAgentClient(options=LiteAgentOptions(profile=profile, cwd=tmp_path, tools=[Lookup()]))


def test_automatic_native_tools_explain_required_endpoint(tmp_path):
    profile = ProfileOptions(harness="codex", model="openai/test")
    with pytest.raises(ConfigurationError, match="api_base"):
        LiteAgentClient(options=LiteAgentOptions(profile=profile, cwd=tmp_path, tools=[Lookup()]))
