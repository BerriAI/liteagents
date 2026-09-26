"""The same public profile and application run through every supported adapter."""

import asyncio
import socket
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

import pytest

from liteagents import (
    AssistantMessage,
    LiteAgentClient,
    LiteAgentOptions,
    ProfileOptions,
    TemporalOptions,
    Tool,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    available_harnesses,
    get_capabilities,
)
from liteagents.runtime.native_protocol import tool_name
from tests.native_provider_fixture import Provider
from tests.test_native_recovery import available


class Lookup(Tool):
    name = "lookup"
    description = "Look up the order total."
    input_schema: ClassVar[dict] = {"type": "object", "properties": {}, "additionalProperties": False}

    def __init__(self):
        self.calls = 0

    async def execute(self, input):
        self.calls += 1
        return "USD 12"


@pytest.mark.integration
@pytest.mark.parametrize("harness", available_harnesses())
async def test_application_tool_alone_selects_supported_execution(harness, tmp_path):
    if harness in ("deepagents", "pydantic-ai"):
        pytest.importorskip(harness.replace("-", "_"))
    else:
        available(harness)
    tool = Lookup()
    async with Provider().running() as provider, asyncio.timeout(60):
        profile = ProfileOptions(
            harness=harness, model="litellm_proxy/scripted",
            model_kwargs={"api_base": provider.url, "api_key": "synthetic"},
        )
        assert profile.recovery is None and profile.tools is None and not profile.mcp_servers
        async with LiteAgentClient(
            options=LiteAgentOptions(profile=profile, tools=[tool], cwd=tmp_path)
        ) as client:
            result = await (await client.start_run("Look up the order")).result()
        assert tool.calls == 1
        assert [b.name for m in result.messages if isinstance(m, AssistantMessage)
                for b in m.content if isinstance(b, ToolUseBlock)] == ["lookup"]


@pytest.mark.integration
@pytest.mark.parametrize("harness", available_harnesses())
@pytest.mark.parametrize("durable", [False, True], ids=["direct", "temporal"])
@pytest.mark.parametrize("selection", ["selected", "none", "defaults"])
async def test_same_application_tools_mcp_and_events(harness, durable, selection, tmp_path):
    if harness in ("deepagents", "pydantic-ai"):
        pytest.importorskip(harness.replace("-", "_"))
    else:
        available(harness)
    pytest.importorskip("mcp")
    if durable:
        pytest.importorskip("temporalio")
        try:
            with socket.create_connection(("127.0.0.1", 7233), timeout=0.2):
                pass
        except OSError:
            pytest.skip("Start Temporal on localhost:7233")
    tool = Lookup()
    async with Provider().running() as provider, AsyncExitStack() as stack, asyncio.timeout(90):
        provider.protocols = {"chat/completions"}
        profile = ProfileOptions(
            harness=harness, model="litellm_proxy/scripted",
            model_kwargs={"api_base": provider.url, "api_key": "synthetic",
                          "temperature": 0, "top_p": 0.9, "max_tokens": 512},
            tools={"selected": ["lookup", "external_slow"], "none": [], "defaults": None}[selection],
            mcp_servers={"external": {
                "command": sys.executable,
                "args": [str(Path(__file__).with_name("portable_mcp_fixture.py"))],
                "allowed_tools": ["slow"],
            }},
            max_turns=6,
        )
        assert profile.recovery is None
        if durable:
            from liteagents.temporal import LiteAgentWorker

            profile.temporal = TemporalOptions(
                profile_id="portable-" + uuid4().hex,
                checkpoint_path=str(tmp_path / "checkpoint.sqlite"),
            )
            await stack.enter_async_context(
                LiteAgentWorker(profile=profile, tools=[tool], cwd=tmp_path).running()
            )
        options = LiteAgentOptions(profile=profile, tools=[] if durable else [tool], cwd=tmp_path)
        async with LiteAgentClient(options=options) as client:
            assert client.capabilities == get_capabilities(profile, tools=[tool])
            assert client.capabilities.custom_tools
            run = await client.start_run("Look up and validate the order")
            result = await run.result()
            events = [event async for event in run.events()]
        if durable:
            async with LiteAgentClient(options=options) as client:
                attached = await client.get_run(run.run_id)
                assert (await attached.result()).messages == result.messages
        calls = [b for m in result.messages if isinstance(m, AssistantMessage)
                 for b in m.content if isinstance(b, ToolUseBlock)]
        returns = [b.tool_use_id for m in result.messages
                   if isinstance(m, UserMessage) and isinstance(m.content, list)
                   for b in m.content if isinstance(b, ToolResultBlock)]
        assert result.text == "Validated USD 12"
        assert provider.paths and set(provider.paths) == {"chat/completions"}
        assert all(r["model"] == "scripted" and r["temperature"] == 0 and r["top_p"] == 0.9
                   and r.get("max_tokens", r.get("max_completion_tokens")) == 512
                   for r in provider.requests)
        assert result.usage.get("native_reports"), "Final native usage must survive normalization"
        assert [b.name for b in calls] == ([] if selection == "none" else ["lookup", "external_slow"])
        assert all(b.id in returns for b in calls)
        assert tool.calls == (0 if selection == "none" else 1)
        recorded = [e.message for e in events if e.message is not None]
        assert [b.name for m in recorded if isinstance(m, AssistantMessage)
                for b in m.content if isinstance(b, ToolUseBlock)] == [b.name for b in calls]
        if harness not in ("deepagents", "pydantic-ai"):
            assert all(b.native_name and b.native_name != b.name for b in calls)
        definitions = [tool_name(child) for request in provider.requests
                       for definition in request.get("tools", [])
                       for child in definition.get("tools", [definition])]
        assert not any("forbidden" in name for name in definitions)
        if selection == "none":
            assert definitions == []
        elif selection == "selected":
            assert definitions and all(
                name.endswith(("lookup", "external_slow")) for name in definitions
            )


@pytest.mark.integration
@pytest.mark.parametrize("harness", available_harnesses())
@pytest.mark.parametrize("durable", [False, True], ids=["direct", "temporal"])
async def test_conversations_and_jobs_keep_their_meaning(harness, durable, tmp_path):
    if harness in ("deepagents", "pydantic-ai"):
        pytest.importorskip(harness.replace("-", "_"))
    else:
        available(harness)
    if durable:
        pytest.importorskip("temporalio")
        try:
            with socket.create_connection(("127.0.0.1", 7233), timeout=0.2):
                pass
        except OSError:
            pytest.skip("Start Temporal on localhost:7233")
    tool = Lookup()
    async with Provider().running() as provider, AsyncExitStack() as stack, asyncio.timeout(90):
        provider.protocols = {"chat/completions"}
        profile = ProfileOptions(
            harness=harness, model="litellm_proxy/scripted",
            model_kwargs={"api_base": provider.url, "api_key": "synthetic", "temperature": 0},
            max_turns=4,
        )
        if durable:
            from liteagents.temporal import LiteAgentWorker

            profile.temporal = TemporalOptions(
                profile_id="conversation-" + uuid4().hex,
                checkpoint_path=str(tmp_path / "checkpoint.sqlite"),
            )
            await stack.enter_async_context(
                LiteAgentWorker(profile=profile, tools=[tool], cwd=tmp_path).running()
            )
        async with LiteAgentClient(
            options=LiteAgentOptions(profile=profile, tools=[tool], cwd=tmp_path)
        ) as client:
            first = [event async for event in client.query("Look up the order")]
            followup_id = "followup-" + uuid4().hex
            following = [event async for event in client.query("Repeat the total", run_id=followup_id)]
            assert tool.calls == 1
            assert any(isinstance(b, ToolUseBlock) for m in first if isinstance(m, AssistantMessage)
                       for b in m.content)
            assert not any(isinstance(b, ToolUseBlock) for m in following if isinstance(m, AssistantMessage)
                           for b in m.content)
            history = list(client.history)
            assert sum(isinstance(m, UserMessage) and isinstance(m.content, str) for m in history) == 2
            for expected in (2, 3):
                result = await (await client.start_run("Look up the order")).result()
                assert result.text == "Validated USD 12"
                assert tool.calls == expected
            assert client.history == history
            if durable:
                attached = await client.get_run(followup_id)
                workflow_history = await attached.handle.fetch_history()
                raw = b"".join(event.SerializeToString() for event in workflow_history.events)
                assert b"USD 12" not in raw, "Tool content must stay out of Temporal history"
