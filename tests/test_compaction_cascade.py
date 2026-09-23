"""Cascades use the same strategy protocol, with one final atomic history edit."""

import asyncio
import json
from copy import deepcopy

import pytest

from liteagents import (
    CompactionCompleted,
    CompactionError,
    CompactionFailed,
    CompactionResult,
    CompactionSkipped,
    CompactionUpdate,
    ContextBudgetExceeded,
    LiteAgentClient,
    LiteAgentOptions,
    PruneToolResults,
    RecentTokens,
    ReplacePrefix,
    ReplaceToolResult,
    Summarize,
    SummaryMessage,
    TurnThreshold,
    UserMessage,
    any_of,
    apply_compaction,
    cascade,
)
from liteagents._internal.compaction_runtime import CompactionRuntime
from liteagents.history import ConversationHistory

from .compaction_helpers import count_chars, old_history, pair, policy, run
from .conftest import text_response
from .test_loop import EchoTool


@pytest.mark.parametrize("target,window,nested,expected_models,stage_count", [
    pytest.param(8000, 100_000, False, ["main"], 1, id="pruning-meets-target"),
    pytest.param(1000, 100_000, False, ["summary", "main"], 2, id="summary-needed"),
    pytest.param(1000, 1500, False, ["summary", "main"], 2, id="intermediate-still-over-budget"),
    pytest.param(100_000, 1500, False, ["summary", "main"], 2, id="target-clamped-to-budget"),
    pytest.param(1000, 100_000, True, ["summary", "main"], 2, id="nested-cascade"),
])
async def test_cascade_reduces_before_main_request_and_replays_as_one_update(
    mock_anthropic_messages, target, window, nested, expected_models, stage_count,
):
    initial = [*old_history(), *pair("old", "raw output " * 1000), *pair("recent", "latest result")]
    prune = PruneToolResults(keep=1)
    strategy = cascade([
        cascade([prune]) if nested else prune,
        Summarize(model="summary", keep=RecentTokens(1), max_tokens=100),
    ])
    if "summary" in expected_models:
        def summarize_pruned_history(**request):
            transcript = request["messages"][0]["content"]
            assert prune.placeholder in transcript
            assert "raw output" not in transcript
            return text_response("checkpoint", model="summary")

        mock_anthropic_messages.push(summarize_pruned_history)
    mock_anthropic_messages.push(text_response("done", model="main"))
    options = LiteAgentOptions(model="main", max_tokens=100, compaction=policy(
        strategy=strategy, trigger=any_of([TurnThreshold(20), TurnThreshold(2)]),
        target_tokens=target, context_windows={"main": window, "summary": 100_000},
    ))
    async with LiteAgentClient(options=options, history=initial) as agent:
        events = [event async for event in agent.query("current request")]
        completed = [e for e in events if isinstance(e, CompactionCompleted)]
        assert len(completed) == 1
        update = completed[0].update
        assert len(update.steps) == stage_count
        assert completed[0].tokens_after <= min(target, window - 100)
        mirror = apply_compaction([*initial, UserMessage("current request")], update)
        assert [*mirror, events[-1]] == agent.history
    assert [call["model"] for call in mock_anthropic_messages.calls] == expected_models


@pytest.mark.parametrize("first", ["none", "growing"], ids=["no-proposal", "non-shrinking-proposal"])
async def test_no_reduction_does_not_block_later_strategy_or_commit_its_state(first):
    observed = []

    class First:
        async def compact(self, context):
            if first == "none":
                return None
            return CompactionResult(
                CompactionUpdate(len(context.messages), prefix=ReplacePrefix(2, "bigger " * 2000)),
                state="must not commit", usage={"input_tokens": 7, "output_tokens": 9},
            )

    class Second:
        async def compact(self, context):
            observed.append(context)
            assert context.state is None
            return CompactionResult(
                CompactionUpdate(len(context.messages), prefix=ReplacePrefix(2, "short")),
                state="committed", usage={"input_tokens": 3, "output_tokens": 2},
            )

    runtime = CompactionRuntime(policy(strategy=cascade([First(), Second()]), target_tokens=1000))
    history = ConversationHistory([*old_history(), UserMessage("current")])
    events = await run(runtime, history)
    assert isinstance(events[-1], CompactionCompleted)
    assert observed[0].messages == (*old_history(), UserMessage("current"))
    assert runtime.state == (None, "committed")
    assert events[-1].usage["input_tokens"] == (10 if first == "growing" else 3)
    assert events[-1].usage["output_tokens"] == (11 if first == "growing" else 2)


@pytest.mark.parametrize("failure", ["error", "invalid-edit", "no-fit"])
async def test_later_failure_rolls_back_all_history_and_state(failure):
    calls = []
    paid = {"input_tokens": 7, "output_tokens": 2, "provider_detail": {"cached": True}}

    class First:
        async def compact(self, context):
            context.state["count"] += 1
            return CompactionResult(
                CompactionUpdate(len(context.messages), prefix=ReplacePrefix(2, "checkpoint " * 100)),
                state=context.state, usage=paid,
            )

    class Second:
        async def compact(self, context):
            calls.append("second")
            assert isinstance(context.messages[0], SummaryMessage)
            assert context.state == {"count": 0}
            if failure == "error":
                raise CompactionError("summary failed", usage={"input_tokens": 3, "output_tokens": 1})
            if failure == "invalid-edit":
                return CompactionResult(CompactionUpdate(99), usage={"input_tokens": 3, "output_tokens": 1})
            return None

    class MustNotRun:
        async def compact(self, context):
            calls.append("fallback")

    runtime = CompactionRuntime(policy(strategy=cascade([First(), Second(), MustNotRun()]),
                                       target_tokens=100, context_windows={"main": 1000}))
    runtime.state = ({"count": 0}, {"count": 0}, None)
    history = ConversationHistory([*old_history(), UserMessage("current")])
    before = deepcopy(history.raw())
    events = []
    with pytest.raises(CompactionError) as error:
        async for event in runtime.run(history=history, model="main", system=None, tools=[], max_tokens=100):
            events.append(event)
    assert history.raw() == before
    assert runtime.state == ({"count": 0}, {"count": 0}, None)
    assert isinstance(events[-1], CompactionFailed)
    assert events[-1].usage["stages"][0] == paid
    assert error.value.usage == events[-1].usage
    if failure == "no-fit":
        assert isinstance(error.value, ContextBudgetExceeded)
        assert calls == ["second", "fallback"]
        assert error.value.usage["input_tokens"] == 7
    else:
        assert calls == ["second"]
        assert error.value.usage["input_tokens"] == 10


async def test_reused_child_has_independent_state_per_position_and_client():
    class ShrinkNextResult:
        async def compact(self, context):
            state = context.state or {"count": 0}
            state["count"] += 1
            for index, message in enumerate(context.messages):
                if isinstance(message, UserMessage) and isinstance(message.content, list):
                    result = message.content[0]
                    if len(result.content) > 100:
                        return CompactionResult(CompactionUpdate(len(context.messages), tool_results=(
                            ReplaceToolResult(index, result.tool_use_id, "short"),
                        )), state=state)
            return None

    shared = ShrinkNextResult()
    options = policy(strategy=cascade([shared, shared]), target_tokens=1)
    first, second = CompactionRuntime(options), CompactionRuntime(options)
    for runtime in (first, first, second):
        history = ConversationHistory([UserMessage("goal"), *pair("a", "x" * 1000), *pair("b", "y" * 1000)])
        events = await run(runtime, history)
        assert isinstance(events[-1], CompactionCompleted)
    assert first.state == ({"count": 2}, {"count": 2})
    assert second.state == ({"count": 1}, {"count": 1})
    assert first.state[0] is not first.state[1]
    assert first.state[0] is not second.state[0]


async def test_preview_counts_raw_blocks_system_and_tools_without_modifying_history():
    observed = []

    class Inspect:
        async def compact(self, context):
            observed.append(context)

    history = ConversationHistory([UserMessage("goal")])
    raw = [{"type": "thinking", "thinking": "reasoning " * 200, "signature": "signed"},
           {"type": "tool_use", "id": "a", "name": "echo", "input": {}}]
    history.add_assistant_response(raw, "main")
    history.add_user_tool_results([{"type": "tool_result", "tool_use_id": "a", "content": "x" * 6000}])
    runtime = CompactionRuntime(policy(strategy=cascade([PruneToolResults(keep=0), Inspect()]), target_tokens=1))
    tools = [EchoTool()]
    events = [event async for event in runtime.run(
        history=history, model="main", system="system " * 100, tools=tools, max_tokens=100,
    )]
    expected = count_chars("main", json.dumps({"messages": history.raw(), "system": "system " * 100,
                                               "tools": [tool.to_anthropic_tool() for tool in tools]}))
    assert observed[0].tokens == expected
    assert events[-1].tokens_after == expected.tokens
    assert history.raw()[1]["content"] == raw
    assert observed[0].message_tokens[1] == len(json.dumps(history.raw()[1]))


async def test_cancellation_after_first_reduction_discards_candidate():
    entered = asyncio.Event()

    class Block:
        async def compact(self, context):
            assert "removed" in context.messages[2].content[0].content
            entered.set()
            await asyncio.Event().wait()

    runtime = CompactionRuntime(policy(strategy=cascade([PruneToolResults(keep=0), Block()]), target_tokens=1))
    history = ConversationHistory([UserMessage("goal"), *pair("a", "x" * 2000)])
    before = deepcopy(history.raw())
    task = asyncio.create_task(run(runtime, history))
    await asyncio.wait_for(entered.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert history.raw() == before
    assert runtime.state is None


@pytest.mark.parametrize("target,expected", [
    pytest.param(None, CompactionError, id="missing-target"),
    pytest.param(100_000, CompactionSkipped, id="already-at-target"),
])
async def test_cascade_requires_a_target_and_does_no_work_when_already_met(target, expected):
    class MustNotRun:
        async def compact(self, context):
            pytest.fail("strategy should not run")

    runtime = CompactionRuntime(policy(strategy=cascade([MustNotRun()]), target_tokens=target))
    history = ConversationHistory([*old_history(), UserMessage("current")])
    if expected is CompactionError:
        with pytest.raises(CompactionError, match="target_tokens"):
            await run(runtime, history)
    else:
        events = await run(runtime, history)
        assert isinstance(events[-1], expected)


@pytest.mark.parametrize("update", [
    pytest.param(CompactionUpdate(2, steps=(CompactionUpdate(3),)), id="wrong-batch-length"),
    pytest.param(CompactionUpdate(3, prefix=ReplacePrefix(2, "summary"), steps=(CompactionUpdate(3),)),
                 id="mixed-batch-and-direct-edits"),
    pytest.param(CompactionUpdate(3, steps=(CompactionUpdate(3, prefix=ReplacePrefix(2, "summary")),
                                          CompactionUpdate(3))), id="stale-child-indices"),
])
def test_invalid_batch_leaves_history_unchanged(update):
    history = ConversationHistory([*old_history(), UserMessage("current")])
    before = deepcopy(history.raw())
    with pytest.raises(ValueError):
        history.compacted(update)
    assert history.raw() == before


def test_cascade_configuration_owns_children_and_rejects_empty_list():
    children = [PruneToolResults()]
    strategy = cascade(children)
    children.clear()
    assert len(strategy.strategies) == 1
    with pytest.raises(ValueError, match="at least one"):
        cascade([])


@pytest.mark.parametrize("target", [0, -1])
def test_target_must_be_positive(target):
    with pytest.raises(ValueError, match="target_tokens"):
        policy(target_tokens=target)
