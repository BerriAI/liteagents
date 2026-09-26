"""Provider translation is internal; native harness controls remain accessible."""

from contextlib import AsyncExitStack
from pathlib import Path

import httpx
import pytest

from liteagents import ConfigurationError, ProfileOptions
from liteagents.harnesses.managed import ManagedAdapter
from liteagents.runtime.control import RunControl
from liteagents.runtime.model_bridge import map_names, translate_request
from liteagents.runtime.model_endpoint import ModelEndpoint
from liteagents.runtime.native_gateway import NativeGateway
from tests.native_provider_fixture import Provider


def test_responses_namespace_roundtrip_preserves_application_tool_identity():
    native = {
        "model": "test",
        "instructions": "Be precise.",
        "input": [
            {"role": "user", "content": "Look up the order"},
            {
                "type": "function_call",
                "namespace": "mcp__liteagents",
                "name": "lookup",
                "arguments": "{}",
                "call_id": "call_1",
            },
            {"type": "function_call_output", "call_id": "call_1", "output": "USD 12"},
        ],
        "tools": [
            {
                "type": "namespace",
                "name": "mcp__liteagents",
                "tools": [
                    {
                        "type": "function",
                        "name": "lookup",
                        "description": "Order lookup",
                        "parameters": {"type": "object", "properties": {}},
                    }
                ],
            }
        ],
    }
    translated = translate_request("responses", native)
    names = map_names(translated, {"lookup"})
    assert names == {"lookup": "mcp__liteagents.lookup"}
    assert translated["tools"][0]["function"]["name"] == "lookup"
    assert translated["messages"][-2]["tool_calls"][0]["function"]["name"] == "lookup"
    assert translated["messages"][-1]["tool_call_id"] == "call_1"
    assert native["input"][1]["namespace"] == "mcp__liteagents"


@pytest.mark.parametrize(
    "harness,config",
    [
        ("codex", {"model_instructions_file": "instructions.md", "features": {"compact": True}}),
        ("opencode-v2", {"instructions": ["instructions.md"], "autoupdate": False}),
    ],
)
async def test_native_controls_survive_managed_adapter(harness, config, monkeypatch, tmp_path):
    captured = []

    class Native:
        def __init__(self, profile, **kwargs):
            self.profile = profile

        async def open(self):
            captured.append(self.profile)

        async def close(self):
            pass

    monkeypatch.setattr(ManagedAdapter, "native_factory", lambda self: Native)
    profile = ProfileOptions(
        harness=harness,
        model="openai/test",
        tools=[],
        harness_options={"config": config, "timeout_seconds": 42},
    )
    adapter = ManagedAdapter(profile, cwd=tmp_path, tools=[], session_id="test")
    async with AsyncExitStack() as stack:
        stack.push_async_callback(adapter.close)
        await adapter.open()
        applied = captured[0].harness_options
        assert applied["timeout_seconds"] == 42
        for name, value in config.items():
            if name == "features":
                assert applied["config"][name]["compact"] is True
            else:
                assert applied["config"][name] == value


@pytest.mark.parametrize(
    "config",
    [
        {"model_providers": {"other": {}}},
        {"mcp_servers": {}},
        {"features": {"shell_tool": True}},
    ],
)
def test_native_controls_cannot_bypass_shared_boundaries(config):
    profile = ProfileOptions(
        harness="codex", model="openai/test", harness_options={"config": config}
    )
    with pytest.raises(ConfigurationError, match="managed|Managed"):
        ManagedAdapter(profile, cwd=Path.cwd(), tools=[], session_id="test")


@pytest.mark.parametrize("stream", [False, True])
async def test_python_endpoint_uses_litellm_and_exact_shared_settings(stream):
    async with Provider().running() as provider:
        provider.protocols = {"chat/completions"}
        profile = ProfileOptions(
            harness="deepagents",
            model="litellm_proxy/scripted",
            model_kwargs={
                "api_base": provider.url,
                "api_key": "synthetic",
                "temperature": 0,
                "stop": ["END"],
                "max_tokens": 500,
            },
        )
        async with ModelEndpoint(profile) as endpoint, httpx.AsyncClient() as client:
            result = await client.post(
                endpoint.url + "/chat/completions",
                json={
                    "model": "native-default-must-not-win",
                    "temperature": 0.8,
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": stream,
                },
            )
            assert result.status_code == 200
            assert "Validated USD 12" in result.text
        request = provider.requests[0]
        assert request["model"] == "scripted"
        assert request["temperature"] == 0
        assert request["stop"] == ["END"]
        assert request.get("max_tokens", request.get("max_completion_tokens")) == 500


@pytest.mark.parametrize("stream", [False, True])
async def test_incomplete_upstream_response_never_commits(stream):
    async with Provider().running() as provider:
        provider.truncate_stream = True
        profile = ProfileOptions(harness="deepagents", model="litellm_proxy/scripted",
                                 model_kwargs={"api_base": provider.url, "api_key": "test"})
        async with ModelEndpoint(profile) as endpoint, httpx.AsyncClient() as client:
            response = await client.post(endpoint.url + "/chat/completions", json={
                "model": "test", "messages": [{"role": "user", "content": "Hello"}],
                "stream": stream,
            })
            if stream:
                assert "liteagents_transient_stream" in response.text
                assert "[DONE]" not in response.text
            else:
                assert response.status_code == 500


async def test_turn_limit_applies_without_tools_and_cannot_be_reset_by_native_lane(monkeypatch):
    profile = ProfileOptions(harness="codex", model="openai/test", tools=[], max_turns=1)
    gateway = NativeGateway(profile, [])
    gateway.bind(RunControl(profile, run_key="test"))

    async def complete(*args, **kwargs):
        return {"body": "{}", "content_type": "application/json", "calls": []}

    monkeypatch.setattr("liteagents.runtime.native_gateway.complete", complete)

    class Request:
        def __init__(self):
            self.match_info = {"path": "v1/responses"}

        async def json(self):
            return {"model": "test", "input": "Hello"}

    await gateway.model_request(Request())
    with pytest.raises(ConfigurationError, match="limit"):
        await gateway.model_request(Request())
