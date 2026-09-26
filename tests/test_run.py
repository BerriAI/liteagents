"""The final-result convenience API uses the same runs, tools, and cleanup."""

import asyncio

import pytest

from liteagents import (
    AssistantMessage,
    HarnessError,
    ProfileOptions,
    TextBlock,
    TextDelta,
    ToolUseBlock,
    run,
)
from liteagents.harnesses.base import HarnessAdapter
from liteagents.runtime import direct
from tests.notebook_provider_fixture import NotebookProvider
from tests.test_native_recovery import available
from tests.test_python_harnesses import Lookup, deep_model, pydantic_model


@pytest.fixture
def adapters(monkeypatch):
    instances = []

    class Adapter(HarnessAdapter):
        async def open(self):
            self.closed = False
            self.prompts = []
            self.started = asyncio.Event()

        async def close(self):
            self.closed = True

        async def query(self, prompt, *, run_id, resume=False):
            self.prompts.append(prompt)
            self.started.set()
            if prompt == "fail":
                raise HarnessError("provider failed")
            if prompt == "wait":
                await asyncio.Event().wait()
            yield TextDelta("partial", "test")
            yield AssistantMessage(
                [TextBlock("thinking"), ToolUseBlock("call1", "lookup", {})],
                "test", "tool_use",
            )
            yield AssistantMessage(
                [TextBlock("Final "), TextBlock("answer")],
                "test", "end_turn", usage={"input_tokens": 5},
            )

    def create(profile, **kwargs):
        adapter = Adapter(profile, **kwargs)
        instances.append(adapter)
        return adapter

    monkeypatch.setattr(direct, "create_adapter", create)
    return instances


async def test_run_returns_final_answer_metadata_and_closes_resources(adapters, tmp_path):
    result = await run(
        "hello", profile=ProfileOptions(harness="pydantic-ai", model="test"),
        cwd=tmp_path, run_id="first",
    )
    assert result.text == "Final answer"
    assert result.run_id == "first"
    assert result.harness == "pydantic-ai"
    assert len(result.messages) == 2
    assert result.usage == {"native_reports": [{"input_tokens": 5}]}
    assert adapters and all(a.closed and a.cwd == tmp_path for a in adapters)


async def test_run_switches_harness_and_starts_fresh_each_time(adapters, tmp_path):
    profile = ProfileOptions(harness="pydantic-ai", model="test")
    first = await run("one", profile=profile, cwd=tmp_path)
    profile.harness = "claude-sdk"
    second = await run("two", profile=profile, cwd=tmp_path)
    assert first.harness == "pydantic-ai" and second.harness == "claude-sdk"
    assert first.run_id != second.run_id
    assert first.session_id != second.session_id
    assert [a.prompts for a in adapters if a.prompts] == [["one"], ["two"]]
    assert all(a.closed for a in adapters)


async def test_run_propagates_failure_and_closes_resources(adapters, tmp_path):
    with pytest.raises(HarnessError, match="provider failed"):
        await run("fail", profile=ProfileOptions(harness="pydantic-ai", model="test"), cwd=tmp_path)
    assert adapters and all(a.closed for a in adapters)


async def test_cancelling_run_closes_local_work(adapters, tmp_path):
    task = asyncio.create_task(run(
        "wait", profile=ProfileOptions(harness="pydantic-ai", model="test"), cwd=tmp_path,
    ))
    async with asyncio.timeout(3):
        while not any(getattr(a, "started", None) and a.started.is_set() for a in adapters):
            await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 3)
    assert all(a.closed for a in adapters)


@pytest.mark.integration
@pytest.mark.parametrize(
    "harness,factory", [("deepagents", deep_model), ("pydantic-ai", pydantic_model)]
)
async def test_run_with_real_native_tool_loop(harness, factory, tmp_path):
    tool = Lookup()
    profile = ProfileOptions(
        harness=harness, model="scripted/test",
        harness_options={"model_instance": factory()},
    )
    result = await run("Look up order A123", profile=profile, tools=[tool], cwd=tmp_path)
    assert result.text == "Order total: USD 12"
    assert result.harness == harness
    assert tool.calls == [{"order": "A123"}]
    assert any(isinstance(b, ToolUseBlock) for m in result.messages
               if isinstance(m, AssistantMessage) for b in m.content)


@pytest.mark.integration
@pytest.mark.parametrize(
    "harness", ["deepagents", "pydantic-ai", "claude-sdk", "codex", "opencode-v1", "opencode-v2"]
)
async def test_run_same_model_and_prompt_across_real_harnesses(harness, tmp_path, monkeypatch):
    if harness in ("deepagents", "pydantic-ai"):
        pytest.importorskip(harness.replace("-", "_"))
    else:
        available(harness)
    async with NotebookProvider().running() as provider, asyncio.timeout(60):
        monkeypatch.setenv("OPENAI_API_KEY", "synthetic-run-key")
        monkeypatch.setenv("OPENAI_API_BASE", provider.url)
        profile = ProfileOptions(harness=harness, model="openai/notebook-model")
        result = await run("Explain what an agent harness does.", profile=profile, cwd=tmp_path)
        assert result.text == "READY"
        assert result.harness == harness
        assert provider.requests
        assert all(r["model"] == "notebook-model" for r in provider.requests)
