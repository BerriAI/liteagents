import asyncio
from contextlib import AsyncExitStack
from uuid import uuid4

import pytest

from liteagents import LiteAgentClient, LiteAgentOptions, ProfileOptions, TemporalOptions
from tests.native_provider_fixture import Provider
from tests.operation_fixture import FixtureTool


@pytest.mark.parametrize("durable", [False, True])
@pytest.mark.parametrize("after_tool", [False, True])
async def test_harness_fallback_only_before_first_tool(tmp_path, durable, after_tool):
    pytest.importorskip("deepagents")
    pytest.importorskip("openai_codex")
    provider = Provider()
    provider.fail_chat = not after_tool
    provider.fail_after_tool = after_tool
    tools = [FixtureTool(name, tmp_path / "calls.txt") for name in ("lookup", "slow")]
    async with provider.running(), AsyncExitStack() as stack, asyncio.timeout(40):
        profile = ProfileOptions(
            harness="deepagents",
            model="litellm_proxy/scripted",
            model_kwargs={"api_base": provider.url, "api_key": "synthetic"},
            tools=["lookup", "slow"],
            recovery={"retries": {"max_attempts": 2}, "harness_fallbacks": ["codex"]},
        )
        if durable:
            from liteagents.temporal import LiteAgentWorker

            profile.temporal = TemporalOptions(
                profile_id="fallback-" + uuid4().hex, checkpoint_path=str(tmp_path / "graph.sqlite")
            )
            await stack.enter_async_context(
                LiteAgentWorker(profile=profile, tools=tools, cwd=tmp_path).running()
            )
        client = await stack.enter_async_context(
            LiteAgentClient(
                options=LiteAgentOptions(
                    profile=profile, tools=[] if durable else tools, cwd=tmp_path
                )
            )
        )
        run = await client.start_run(
            "Read and validate the order", run_id="fallback-" + uuid4().hex
        )
        if after_tool:
            with pytest.raises(Exception, match="failed"):
                await run.result()
            assert (tmp_path / "calls.txt").read_text().splitlines() == ["lookup"]
            assert not any([e.kind == "harness_fallback" async for e in run.events()])
        else:
            result = await run.result()
            assert result.harness == "codex"
            assert result.text == "Validated USD 12"
            assert (tmp_path / "calls.txt").read_text().splitlines() == [
                "lookup",
                "slow-start",
                "slow-done",
            ]
            assert any([e.kind == "harness_fallback" async for e in run.events()])
