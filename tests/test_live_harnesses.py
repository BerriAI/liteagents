"""Opt-in provider acceptance: LITEAGENTS_LIVE=1 plus gateway/model env vars."""

import asyncio
import os
import sys
from pathlib import Path

import pytest

from liteagents import (
    AssistantMessage,
    LiteAgentClient,
    LiteAgentOptions,
    ProfileOptions,
    TextDelta,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("LITEAGENTS_LIVE") != "1", reason="Real provider tests are opt-in"
    ),
]
HARNESSES = ["deepagents", "pydantic-ai", "claude-sdk", "codex", "opencode-v1", "opencode-v2"]


def live_profile(harness, *, mcp=False):
    model = os.environ["LITEAGENTS_CLAUDE_MODEL" if harness == "claude-sdk" else "LITEAGENTS_MODEL"]
    options = {"timeout_seconds": 120} if harness in HARNESSES[2:] else {}
    tools = [] if harness == "codex" else ["read_file"]
    servers = {}
    if mcp:
        config = {
            "command": sys.executable,
            "args": [str(Path(__file__).with_name("mcp_fixture_server.py"))],
        }
        if harness == "codex":
            config["enabled_tools"] = ["read_memory"]
            config["tools"] = {"read_memory": {"approval_mode": "approve"}}
        if harness in HARNESSES[:2]:
            config["allowed_tools"] = ["read_memory"]
        servers = {"evidence": config}
        tools = (
            ["evidence_read_memory"]
            if harness in HARNESSES[:2]
            else ["mcp__evidence__read_memory"]
            if harness == "claude-sdk"
            else []
        )
    return ProfileOptions(
        harness=harness,
        model="litellm_proxy/" + model,
        model_kwargs={
            "api_base": os.environ["LITEAGENTS_API_BASE"],
            "api_key": os.environ["LITELLM_API_KEY"],
        },
        tools=tools,
        mcp_servers=servers,
        harness_options=options,
        features={"streaming": not mcp},
        system_prompt="Use the requested tool, report its actual result, and be concise.",
    )


@pytest.mark.parametrize("harness", HARNESSES)
async def test_live_tool_stream_and_followup(harness, tmp_path):
    (tmp_path / "input.txt").write_text("The verification code is CORAL-731.\n")
    async with asyncio.timeout(150):
        async with LiteAgentClient(
            options=LiteAgentOptions(profile=live_profile(harness), cwd=tmp_path)
        ) as client:
            events = [
                e
                async for e in client.query(
                    "Read input.txt with your file tool. Report the code.", run_id="read"
                )
            ]
            result = await (await client.get_run("read")).result()
            assert "CORAL-731" in result.text
            calls = [
                b.id
                for e in events
                if isinstance(e, AssistantMessage)
                for b in e.content
                if isinstance(b, ToolUseBlock)
            ]
            returns = [
                b.tool_use_id
                for e in events
                if isinstance(e, UserMessage) and isinstance(e.content, list)
                for b in e.content
                if isinstance(b, ToolResultBlock)
            ]
            assert calls and set(calls) <= set(returns)
            assert any(isinstance(e, TextDelta) for e in events)
            _ = [
                e
                async for e in client.query(
                    "What was the code? Answer from our conversation without tools.",
                    run_id="followup",
                )
            ]
            assert "CORAL-731" in (await (await client.get_run("followup")).result()).text


@pytest.mark.parametrize("harness", HARNESSES)
async def test_live_native_mcp(harness, tmp_path):
    async with asyncio.timeout(150):
        async with LiteAgentClient(
            options=LiteAgentOptions(profile=live_profile(harness, mcp=True), cwd=tmp_path)
        ) as client:
            events = [
                e
                async for e in client.query(
                    'Call the evidence server read_memory tool with query="mcp-verified". Do not use write_memory. Report the exact returned evidence.',
                    run_id="mcp",
                )
            ]
            result = await (await client.get_run("mcp")).result()
            assert "evidence:mcp-verified" in result.text
            assert any(
                isinstance(b, ToolUseBlock) and "read_memory" in b.name
                for e in events
                if isinstance(e, AssistantMessage)
                for b in e.content
            )
            assert any(
                isinstance(b, ToolResultBlock) and "evidence:mcp-verified" in str(b.content)
                for e in events
                if isinstance(e, UserMessage) and isinstance(e.content, list)
                for b in e.content
            )


@pytest.mark.parametrize("harness", HARNESSES[2:])
async def test_live_reopen_native_session(harness, tmp_path):
    profile = live_profile(harness)
    profile.features.streaming = False
    async with asyncio.timeout(120):
        async with LiteAgentClient(
            options=LiteAgentOptions(profile=profile, cwd=tmp_path)
        ) as client:
            _ = [
                e
                async for e in client.query(
                    "Remember the code PEARL-429. Say noted.", run_id="first"
                )
            ]
            session = (await (await client.get_run("first")).result()).session_id
        async with LiteAgentClient(
            options=LiteAgentOptions(profile=profile, cwd=tmp_path, session_id=session)
        ) as client:
            _ = [
                e
                async for e in client.query("What code did I ask you to remember?", run_id="second")
            ]
            assert "PEARL-429" in (await (await client.get_run("second")).result()).text


async def test_live_deepagents_temporal(tmp_path):
    from uuid import uuid4

    from liteagents import TemporalOptions
    from liteagents.temporal import LiteAgentWorker

    profile = live_profile("deepagents")
    profile.features.streaming = False
    profile.temporal = TemporalOptions(
        profile_id="live-" + uuid4().hex, checkpoint_path=str(tmp_path / "graph.sqlite")
    )
    (tmp_path / "input.txt").write_text("The verification code is CORAL-731.\n")
    async with asyncio.timeout(120), LiteAgentWorker(profile=profile, cwd=tmp_path).running():
        async with LiteAgentClient(options=LiteAgentOptions(profile=profile)) as client:
            run = await client.start_run(
                "Read input.txt using the file tool and report the code.",
                run_id="live-" + uuid4().hex,
            )
        async with LiteAgentClient(options=LiteAgentOptions(profile=profile)) as attached:
            result = await (await attached.get_run(run.run_id)).result()
            assert "CORAL-731" in result.text
