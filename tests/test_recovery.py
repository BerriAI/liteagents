import asyncio

import pytest

from liteagents import LiteAgentClient, LiteAgentOptions, ProfileOptions
from liteagents.runtime.control import CURRENT, RunControl, operation_id
from liteagents.storage import RunStore
from tests.test_python_harnesses import Lookup, deep_model, pydantic_model


@pytest.mark.parametrize(
    "harness,factory", [("deepagents", deep_model), ("pydantic-ai", pydantic_model)]
)
async def test_native_approval_blocks_tool_until_decision(harness, factory, tmp_path):
    tool = Lookup()
    profile = ProfileOptions(
        harness=harness,
        model="scripted/test",
        tools=["lookup"],
        harness_options={"model_instance": factory(), "interrupt_on": {"lookup": True}},
    )
    async with LiteAgentClient(
        options=LiteAgentOptions(profile=profile, tools=[tool], cwd=tmp_path)
    ) as client:
        run = await client.start_run("lookup order")
        async with asyncio.timeout(5):
            async for event in run.events():
                if event.kind == "approval_requested":
                    assert not tool.calls
                    assert (await run.approvals())[0]["arguments"] == {"order": "A123"}
                    await run.approve(event.data["id"])
                    break
        assert (await asyncio.wait_for(run.result(), 5)).text == "Order total: USD 12"
        assert tool.calls == [{"order": "A123"}]
        assert await run.approvals() == []
        await run.approve(event.data["id"])
        assert await run.status() == "completed"
        assert [e async for e in run.events(after=0)]


@pytest.mark.parametrize(
    "harness,factory", [("deepagents", deep_model), ("pydantic-ai", pydantic_model)]
)
async def test_native_tool_retries_at_operation_boundary(harness, factory, tmp_path):
    class Flaky(Lookup):
        async def execute(self, arguments):
            self.calls.append(arguments)
            if len(self.calls) < 3:
                raise TimeoutError("transient tool failure")
            assert operation_id()
            return "USD 12"

    tool = Flaky()
    profile = ProfileOptions(
        harness=harness,
        model="scripted/test",
        tools=["lookup"],
        recovery={"retries": {"max_attempts": 3}},
        harness_options={"model_instance": factory()},
    )
    async with LiteAgentClient(
        options=LiteAgentOptions(profile=profile, tools=[tool], cwd=tmp_path)
    ) as client:
        run = await client.start_run("lookup")
        assert (await asyncio.wait_for(run.result(), 5)).text == "Order total: USD 12"
        assert len(tool.calls) == 3
        assert len([e async for e in run.events() if e.kind == "operation_retry"]) == 2


async def test_cancel_before_background_run_starts(tmp_path):
    profile = ProfileOptions(
        harness="pydantic-ai",
        model="scripted/test",
        tools=["lookup"],
        harness_options={"model_instance": pydantic_model()},
    )
    async with LiteAgentClient(
        options=LiteAgentOptions(profile=profile, tools=[Lookup()], cwd=tmp_path)
    ) as client:
        run = await client.start_run("lookup")
        await run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(run.result(), 2)
        await asyncio.sleep(0)
        next_run = await client.start_run("lookup again")
        assert (await next_run.result()).text


async def test_completed_operations_replay_and_idempotency_key(tmp_path):
    store = RunStore("sqlite:///" + str(tmp_path / "state.sqlite"))
    await store.setup()
    await store.ensure_run("run", "v1")
    profile = ProfileOptions(
        harness="pydantic-ai", model="scripted/test", recovery={"retries": {"max_attempts": 3}}
    )
    calls = []

    async def tool():
        calls.append(operation_id())
        return {"value": 42}

    for _ in range(2):
        control = RunControl(profile, run_key="run", store=store)
        assert await control.call("tool", "call1", {"name": "lookup", "input": {}}, tool) == {
            "value": 42
        }
    assert len(calls) == 1 and calls[0]
    assert CURRENT.get() is None


async def test_recovery_stream_is_live_before_model_completion(tmp_path):
    pytest.importorskip("pydantic_ai")
    from pydantic_ai.models.function import FunctionModel

    from liteagents import TextDelta

    release = asyncio.Event()

    async def tokens(messages, info):
        yield "First "
        await release.wait()
        yield "second"

    profile = ProfileOptions(
        harness="pydantic-ai",
        model="scripted/test",
        features={"streaming": True},
        recovery={},
        harness_options={"model_instance": FunctionModel(stream_function=tokens)},
    )
    async with LiteAgentClient(options=LiteAgentOptions(profile=profile, cwd=tmp_path)) as client:
        from contextlib import aclosing

        async with aclosing(client.query("stream", run_id="stream")) as events:
            async with asyncio.timeout(5):
                first = await anext(events)
                assert isinstance(first, TextDelta) and first.text == "First "
                assert not release.is_set()
                release.set()
                rest = [event async for event in events]
        result = await (await client.get_run("stream")).result()
        assert result.text == "First second"
        assert "".join(e.text for e in [first, *rest] if isinstance(e, TextDelta)) == "First second"


@pytest.mark.parametrize(
    "harness,factory", [("deepagents", deep_model), ("pydantic-ai", pydantic_model)]
)
async def test_native_model_fallback_preserves_tool_results(harness, factory, tmp_path):
    pytest.importorskip("deepagents" if harness == "deepagents" else "pydantic_ai")
    count = []
    if harness == "deepagents":

        class Failing(type(factory())):
            def _generate(self, *args, **kwargs):
                count.append(1)
                raise TimeoutError("temporary model failure")

        primary = Failing()
    else:
        from pydantic_ai.models.function import FunctionModel

        def fail(messages, info):
            count.append(1)
            raise TimeoutError("temporary model failure")

        primary = FunctionModel(fail)
    tool = Lookup()
    profile = ProfileOptions(
        harness=harness,
        model="scripted/primary",
        tools=["lookup"],
        recovery={"retries": {"max_attempts": 2}, "model_fallbacks": ["scripted/backup"]},
        harness_options={"model_instance": primary, "fallback_model_instances": [factory()]},
    )
    async with LiteAgentClient(
        options=LiteAgentOptions(profile=profile, tools=[tool], cwd=tmp_path)
    ) as client:
        run = await client.start_run("lookup order")
        assert (await asyncio.wait_for(run.result(), 5)).text == "Order total: USD 12"
        assert len(tool.calls) == 1
        assert len(count) == 4  # Two attempts at each of two native model boundaries.
        assert len([e async for e in run.events() if e.kind == "model_fallback"]) == 2


async def test_retry_budget_and_inputs_survive_new_control(tmp_path):
    from liteagents.errors import ConfigurationError
    from liteagents.runtime.control import OperationFailed

    store = RunStore("sqlite:///" + str(tmp_path / "state.sqlite"))
    await store.setup()
    await store.ensure_run("budget", "v1")
    profile = ProfileOptions(
        harness="pydantic-ai", model="scripted/test", recovery={"retries": {"max_attempts": 2}}
    )
    calls = []

    async def failing():
        calls.append(1)
        raise TimeoutError("temporary")

    for _ in range(2):
        with pytest.raises(OperationFailed):
            await RunControl(profile, run_key="budget", store=store).call(
                "model", "one", {}, failing
            )
    assert len(calls) == 2

    async def interrupted():
        raise asyncio.CancelledError()

    control = RunControl(profile, run_key="budget", store=store)
    with pytest.raises(asyncio.CancelledError):
        await control.call("model", "two", {"prompt": "original"}, interrupted)
    with pytest.raises(ConfigurationError, match="different inputs"):
        await RunControl(profile, run_key="budget", store=store).call(
            "model", "two", {"prompt": "changed"}, failing
        )
    assert len(calls) == 2


async def test_deepagents_cannot_dispatch_hidden_native_tool(tmp_path):
    pytest.importorskip("deepagents")
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class Adversarial(type(deep_model())):
        def bind_tools(self, tools, **kwargs):
            assert {tool.name for tool in tools} == {"read_file"}
            return self

        def _generate(self, *args, **kwargs):
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "id": "attack",
                                    "name": "write_file",
                                    "args": {
                                        "file_path": "/forbidden.txt",
                                        "content": "should not exist",
                                    },
                                }
                            ],
                        )
                    )
                ]
            )

    profile = ProfileOptions(
        harness="deepagents",
        model="scripted/test",
        tools=["read_file"],
        harness_options={"model_instance": Adversarial()},
    )
    async with LiteAgentClient(options=LiteAgentOptions(profile=profile, cwd=tmp_path)) as client:
        run = await client.start_run("Read a file")
        from liteagents.errors import HarnessError

        with pytest.raises(HarnessError, match="outside this agent tool selection"):
            await run.result()
    assert not (tmp_path / "forbidden.txt").exists()
