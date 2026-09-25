import asyncio
from contextlib import aclosing

import pytest

from liteagents import (
    AssistantMessage,
    ConfigurationError,
    HarnessError,
    LiteAgentClient,
    LiteAgentOptions,
    ProfileOptions,
    TextBlock,
    UnsupportedFeatureError,
)
from liteagents.harnesses.base import HarnessAdapter
from liteagents.runtime import direct
from tests.test_python_harnesses import Lookup, deep_model, pydantic_model


class ControlledAdapter(HarnessAdapter):
    async def query(self, prompt, *, run_id, resume=False):
        if prompt == "fail":
            raise HarnessError("provider failed")
        yield AssistantMessage([TextBlock("started")], "test", "end_turn")
        await asyncio.Event().wait()


@pytest.fixture
def controlled(monkeypatch, tmp_path):
    monkeypatch.setattr(direct, "create_adapter", lambda p, **kw: ControlledAdapter(p, **kw))
    return LiteAgentClient(
        options=LiteAgentOptions(
            profile=ProfileOptions(harness="deepagents", model="test"), cwd=tmp_path
        )
    )


async def test_close_stream_cancels_handle_and_releases_conversation(controlled):
    async with controlled as client:
        async with aclosing(client.query("start", run_id="one")) as stream:
            await anext(stream)
            with pytest.raises(ConfigurationError, match="Concurrent"):
                await anext(client.query("other"))
        run = await client.get_run("one")
        assert run.status == "cancelled"
        with pytest.raises(asyncio.CancelledError):
            await run.result()
        async with aclosing(client.query("again", run_id="two")) as stream:
            await anext(stream)


async def test_failed_run_retains_error(controlled):
    async with controlled as client:
        with pytest.raises(HarnessError, match="provider failed"):
            await anext(client.query("fail", run_id="failure"))
        run = await client.get_run("failure")
        assert run.status == "failed"
        with pytest.raises(HarnessError, match="provider failed"):
            await run.result()


@pytest.mark.parametrize(
    "harness,factory", [("deepagents", deep_model), ("pydantic-ai", pydantic_model)]
)
async def test_model_call_limit_prevents_second_request(harness, factory, tmp_path):
    tool = Lookup()
    profile = ProfileOptions(
        harness=harness,
        model="scripted/test",
        tools=["lookup"],
        max_turns=1,
        harness_options={"model_instance": factory()},
    )
    async with LiteAgentClient(
        options=LiteAgentOptions(profile=profile, tools=[tool], cwd=tmp_path)
    ) as client:
        with pytest.raises(HarnessError, match="limit"):
            _ = [e async for e in client.query("Look up order", run_id="limited")]
        assert len(tool.calls) == 1
        assert (await client.get_run("limited")).status == "failed"


@pytest.mark.parametrize(
    "harness,dependency", [("deepagents", "deepagents"), ("pydantic-ai", "pydantic_ai")]
)
def test_memory_only_harnesses_reject_persistent_session(harness, dependency, tmp_path):
    pytest.importorskip(dependency)
    with pytest.raises(UnsupportedFeatureError, match="history lasts"):
        LiteAgentClient(
            options=LiteAgentOptions(
                profile=ProfileOptions(harness=harness, model="test"),
                cwd=tmp_path,
                session_id="old",
            )
        )


async def test_deepagents_unknown_model_setting_fails_before_request(tmp_path):
    pytest.importorskip("deepagents")
    profile = ProfileOptions(
        harness="deepagents", model="openai/test", model_kwargs={"temprature": 0.5}
    )
    with pytest.raises(ConfigurationError, match="temprature"):
        async with LiteAgentClient(options=LiteAgentOptions(profile=profile, cwd=tmp_path)):
            pytest.fail("invalid profile opened")


@pytest.mark.parametrize("harness", ["deepagents", "pydantic-ai"])
async def test_owned_provider_clients_close_with_adapter(harness, tmp_path, monkeypatch):
    import importlib

    pytest.importorskip("deepagents" if harness == "deepagents" else "pydantic_ai")
    module = importlib.import_module(
        "liteagents.harnesses." + ("deepagents" if harness == "deepagents" else "pydantic_ai")
    )
    original = module.build_model
    captured = []
    if harness == "deepagents":

        async def build(*args):
            model = await original(*args)
            captured.append(model.root_async_client)
            return model
    else:

        def build(*args):
            model, settings = original(*args)
            captured.append(model.client)
            return model, settings

    monkeypatch.setattr(module, "build_model", build)
    profile = ProfileOptions(
        harness=harness, model="openai/test", model_kwargs={"api_key": "synthetic-key"}
    )
    async with LiteAgentClient(options=LiteAgentOptions(profile=profile, cwd=tmp_path)):
        assert not captured[0].is_closed()
    assert captured[0].is_closed()


@pytest.mark.parametrize("run_id", ["", "  "])
async def test_empty_run_id_is_rejected(controlled, run_id):
    async with controlled as client:
        with pytest.raises(ConfigurationError, match="nonempty"):
            await anext(client.query("start", run_id=run_id))


def test_codex_profile_roundtrip_preserves_unspecified_limits(tmp_path):
    pytest.importorskip("openai_codex")
    from liteagents.harnesses.codex import CodexAdapter

    profile = ProfileOptions(harness="codex", model="openai/test")
    restored = ProfileOptions.model_validate(profile.model_dump())
    CodexAdapter(restored, cwd=tmp_path, tools=[], session_id="test")
    assert restored == profile
