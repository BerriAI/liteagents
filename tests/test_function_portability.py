"""One application and provider setup; change only the harness selector."""

import socket
import sys
from contextlib import AsyncExitStack
from pathlib import Path

import pytest

from liteagents import (
    AssistantMessage,
    LiteAgentClient,
    ProfileOptions,
    TemporalOptions,
    ToolUseBlock,
    available_harnesses,
    operation_id,
)
from tests.native_provider_fixture import Provider
from tests.test_native_recovery import available


@pytest.mark.integration
@pytest.mark.parametrize("durable", [False, True], ids=["direct", "temporal"])
async def test_one_setup_switches_every_harness_with_function_and_mcp(durable, tmp_path, monkeypatch):
    for harness in available_harnesses():
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

    calls = []

    def lookup(order: str = "A123") -> dict:
        """Look up the confirmed order total."""
        calls.append((order, operation_id()))
        return {"order": order, "total": "USD 12"}

    # This is the complete application. No harness-specific imports or branches.
    async def application(profile):
        async with LiteAgentClient(profile=profile, tools=[lookup], cwd=tmp_path) as agent:
            handle = await agent.start_run("Look up and validate order A123")
            result = await handle.result()
            names = [b.name for m in result.messages if isinstance(m, AssistantMessage)
                     for b in m.content if isinstance(b, ToolUseBlock)]
            return result.text, names

    async with Provider().running() as provider:
        monkeypatch.setenv("OPENAI_API_KEY", "synthetic-function-key")
        monkeypatch.setenv("OPENAI_API_BASE", provider.url)
        profile = ProfileOptions(
            harness="pydantic-ai", model="openai/scripted", max_turns=6,
            model_kwargs={"temperature": 0},
            harness_options={
                "pydantic-ai": {"tool_timeout": 30},
                "deepagents": {"debug": False},
                "claude-sdk": {"max_budget_usd": 1},
                "codex": {"timeout_seconds": 60},
                "opencode-v1": {"timeout_seconds": 60, "state_dir": str(tmp_path / "oc1")},
                "opencode-v2": {"timeout_seconds": 60, "state_dir": str(tmp_path / "oc2")},
            },
            mcp_servers={"external": {
                "command": sys.executable,
                "args": [str(Path(__file__).with_name("portable_mcp_fixture.py"))],
                "allowed_tools": ["slow"],
            }},
        )
        if durable:
            profile.temporal = TemporalOptions(checkpoint_path=str(tmp_path / "state.sqlite"))
        setup = profile.model_dump(exclude={"harness"})
        for harness in available_harnesses():
            profile.harness = harness
            async with AsyncExitStack() as stack:
                if durable:
                    from liteagents.temporal import LiteAgentWorker

                    await stack.enter_async_context(
                        LiteAgentWorker(profile=profile, tools=[lookup], cwd=tmp_path).running()
                    )
                assert await application(profile) == ("Validated USD 12", ["lookup", "external_slow"])
            assert profile.model_dump(exclude={"harness"}) == setup
        assert len(calls) == 6 and all(order == "A123" and key for order, key in calls)
        assert all(r["model"] == "scripted" and r["temperature"] == 0 for r in provider.requests)
