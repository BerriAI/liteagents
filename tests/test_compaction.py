from __future__ import annotations

import asyncio
import json
from contextlib import aclosing
from copy import deepcopy

import pytest

from liteagents import (
    AssistantMessage,
    CompactionCompleted,
    CompactionError,
    CompactionFailed,
    CompactionResult,
    CompactionSkipped,
    CompactionStarted,
    CompactionUpdate,
    ContextBudgetExceeded,
    FusionOptions,
    LiteAgentClient,
    LiteAgentOptions,
    PruneToolResults,
    RecentTokens,
    ReplacePrefix,
    ReplaceToolResult,
    Summarize,
    SummaryMessage,
    TextDelta,
    TokenEstimate,
    TokenThreshold,
    UserMessage,
    apply_compaction,
    query,
)
from liteagents._internal.compaction_runtime import CompactionRuntime
from liteagents.history import ConversationHistory

from .compaction_helpers import count_chars, old_history, pair, policy, run
from .conftest import text_response, tool_use_response
from .test_loop import EchoTool
from .test_streaming import Stream, response_events


async def test_automatic_summary_preserves_prompt_system_and_usage(mock_anthropic_messages):
    mock_anthropic_messages.push(text_response("checkpoint", model="summary"))
    mock_anthropic_messages.push(text_response("answer", model="main"))
    options = LiteAgentOptions(model="main", compaction=policy(), system="Keep identifiers exact",
                               model_kwargs={"api_base": "https://gateway.invalid", "api_key": "secret"})
    initial = old_history()
    async with LiteAgentClient(options=options, history=initial) as agent:
        events = [event async for event in agent.query("current request")]
        assert [type(event) for event in events] == [CompactionStarted, CompactionCompleted, AssistantMessage]
        completed = events[1]
        assert completed.tokens_after < completed.tokens_before
        assert completed.token_source == "test_chars"
        assert completed.usage == {"input_tokens": 1, "output_tokens": 1}
        assert isinstance(agent.history[0], SummaryMessage)
        assert agent.history[1].content == "current request"
        # Stateless consumers can apply exactly the same edit before appending the answer.
        mirrored = apply_compaction([*initial, UserMessage("current request")], completed.update)
        assert [*mirrored, events[-1]] == agent.history
        agent.history[0].content = "external mutation"
        assert "checkpoint" in agent.history[0].content
    summary_call, main_call = mock_anthropic_messages.calls
    assert summary_call["model"] == "summary"
    assert summary_call["stream"] is False and summary_call["tools"] is None
    assert summary_call["api_base"] == "https://gateway.invalid"
    assert main_call["system"] == "Keep identifiers exact"
    assert main_call["messages"][1]["content"] == "current request"
    assert initial == old_history()


async def test_manual_only_and_instructions_do_not_advance_turn(mock_anthropic_messages):
    mock_anthropic_messages.push(text_response("first answer", model="main"))
    mock_anthropic_messages.push(text_response("manual checkpoint", model="summary"))
    mock_anthropic_messages.push(text_response("next answer", model="main"))
    turns = []

    class Router:
        async def route(self, context):
            turns.append(context.turn)
            return "main"

    options = LiteAgentOptions(model_router=Router(), compaction=policy(trigger=None))
    async with LiteAgentClient(options=options, history=old_history()) as agent:
        with pytest.raises(ValueError, match="Pass model"):
            await agent.compact()
        events = [event async for event in agent.query("current")]
        assert len(events) == 1
        completed = await agent.compact(instructions="Preserve all issue IDs")
        assert completed.reason == "manual"
        assert turns == [1]
        _ = [event async for event in agent.query("next")]
        assert turns == [1, 2]
    assert "Preserve all issue IDs" in mock_anthropic_messages.calls[1]["system"]


async def test_compaction_inside_one_long_tool_turn_never_reexecutes_tools(mock_anthropic_messages):
    calls = []

    class LargeTool(EchoTool):
        async def execute(self, input):
            calls.append(input)
            return "tool output " * 1000

    # First round is small. After its result only the current tool group can be kept.
    # After the second round, the first completed group can be summarized.
    mock_anthropic_messages.push(tool_use_response(tool_use_id="a", name="echo", input={"text": "a"}, model="main"))
    mock_anthropic_messages.push(text_response("goal checkpoint", model="summary"))
    mock_anthropic_messages.push(tool_use_response(tool_use_id="b", name="echo", input={"text": "b"}, model="main"))
    mock_anthropic_messages.push(text_response("first tool completed", model="summary"))
    mock_anthropic_messages.push(text_response("done", model="main"))
    options = LiteAgentOptions(model="main", tools=[LargeTool()], compaction=policy(), max_turns=3)
    async with LiteAgentClient(options=options) as agent:
        events = [event async for event in agent.query("original request " * 100)]
        assert len(calls) == 2
        assert events[-1].content[0].text == "done"
        assert agent.history[1].content == "original request " * 100
    final_call = mock_anthropic_messages.calls[-1]
    assert final_call["messages"][-2]["content"][0]["id"] == "b"
    assert final_call["messages"][-1]["content"][0]["tool_use_id"] == "b"
    assert "tool output" in mock_anthropic_messages.calls[-2]["messages"][0]["content"]


@pytest.mark.parametrize("keep,pruned_ids,outcome", [
    pytest.param(0, {"a", "b"}, CompactionCompleted, id="prune-all"),
    pytest.param(1, {"a"}, CompactionCompleted, id="keep-latest"),
    pytest.param(2, set(), CompactionSkipped, id="keep-everything"),
])
async def test_pruning_keeps_latest_results_and_is_idempotent(mock_anthropic_messages, keep, pruned_ids, outcome):
    placeholder = "[pruned]"
    messages = [UserMessage("goal"), *pair("a", "large output " * 200), *pair("b", "latest output"), UserMessage("continue")]
    runtime = CompactionRuntime(policy(strategy=PruneToolResults(keep=keep, placeholder=placeholder)))
    history = ConversationHistory(messages)
    events = await run(runtime, history)
    assert isinstance(events[-1], outcome)
    if isinstance(events[-1], CompactionCompleted):
        assert history.messages == apply_compaction(messages, events[-1].update)
    expected = deepcopy(messages)
    for index in (2, 4):
        result = expected[index].content[0]
        if result.tool_use_id in pruned_ids:
            result.content = placeholder
    assert history.messages == expected
    events = await run(runtime, history, manual=True)
    assert isinstance(events[-1], CompactionSkipped)
    assert mock_anthropic_messages.calls == []


async def test_retained_raw_blocks_survive_prefix_and_tool_edits():
    history = ConversationHistory([*old_history(), UserMessage("current")])
    raw = [
        {"type": "thinking", "thinking": "private reasoning", "signature": "signed"},
        {"type": "tool_use", "id": "t", "name": "echo", "input": {}, "cache_control": {"type": "ephemeral"}},
    ]
    history.add_assistant_response(raw, "main")
    history.add_user_tool_results([{"type": "tool_result", "tool_use_id": "t", "content": "x" * 2000,
                                   "is_error": True, "provider_metadata": {"keep": 1}}])
    update = CompactionUpdate(len(history.messages), prefix=ReplacePrefix(2, "checkpoint"),
                              tool_results=(ReplaceToolResult(4, "t", "pruned"),))
    candidate = history.compacted(update)
    history.commit(candidate, expected_version=history.version)
    assert history.raw()[2]["content"] == raw
    assert history.raw()[3]["content"][0] == {"type": "tool_result", "tool_use_id": "t", "content": "pruned",
                                             "is_error": True, "provider_metadata": {"keep": 1}}


@pytest.mark.parametrize("update", [
    pytest.param(CompactionUpdate(3, prefix=ReplacePrefix(2, "summary")), id="splits-tool-pair"),
    pytest.param(CompactionUpdate(3, prefix=ReplacePrefix(4, "summary")), id="prefix-out-of-range"),
    pytest.param(CompactionUpdate(3, prefix=ReplacePrefix(1, "")), id="empty-summary"),
    pytest.param(CompactionUpdate(3, tool_results=(ReplaceToolResult(1, "a"),)), id="edit-addresses-tool-call"),
    pytest.param(CompactionUpdate(3, tool_results=(ReplaceToolResult(2, "missing"),)), id="unknown-tool-id"),
    pytest.param(CompactionUpdate(3, tool_results=(ReplaceToolResult(2, "a"), ReplaceToolResult(2, "a"))),
                 id="duplicate-result-edit"),
    pytest.param(CompactionUpdate(3, prefix=ReplacePrefix(3, "summary"), tool_results=(ReplaceToolResult(2, "a"),)),
                 id="prefix-overlaps-result-edit"),
    pytest.param(CompactionUpdate(2, prefix=ReplacePrefix(1, "summary")), id="wrong-history-length"),
])
def test_invalid_edits_do_not_mutate_history(update):
    history = ConversationHistory([UserMessage("goal"), *pair("a", "result")])
    before = deepcopy(history.raw())
    with pytest.raises(ValueError):
        history.compacted(update)
    assert history.raw() == before


@pytest.mark.parametrize("response", [
    pytest.param(text_response("partial", model="summary", stop_reason="max_tokens"), id="truncated-summary"),
    pytest.param(text_response("", model="summary"), id="empty-summary"),
    pytest.param(tool_use_response(tool_use_id="t", name="echo", input={}, model="summary"), id="tool-call-instead-of-summary"),
])
async def test_failed_summaries_leave_history_untouched(mock_anthropic_messages, response):
    mock_anthropic_messages.push(response)
    history = ConversationHistory([*old_history(), UserMessage("current")])
    before = deepcopy(history.raw())
    runtime = CompactionRuntime(policy())
    events = []
    with pytest.raises(CompactionError):
        async for event in runtime.run(history=history, model="main", system=None, tools=[], max_tokens=100):
            events.append(event)
    assert isinstance(events[0], CompactionStarted)
    assert isinstance(events[-1], CompactionFailed)
    assert events[-1].usage == {"input_tokens": 1, "output_tokens": 1}
    assert history.raw() == before


async def test_larger_summary_is_skipped_without_committing(mock_anthropic_messages):
    mock_anthropic_messages.push(text_response("much bigger " * 2000, model="summary"))
    history = ConversationHistory([*old_history(), UserMessage("current")])
    before = deepcopy(history.raw())
    events = await run(CompactionRuntime(policy()), history)
    assert isinstance(events[-1], CompactionSkipped)
    assert events[-1].usage == {"input_tokens": 1, "output_tokens": 1}
    assert history.raw() == before


async def test_summary_model_must_fit_before_api_call(mock_anthropic_messages):
    runtime = CompactionRuntime(policy(context_windows={"main": 100_000, "summary": 500}))
    history = ConversationHistory([*old_history(), UserMessage("current")])
    with pytest.raises(ContextBudgetExceeded, match="Summary input"):
        await run(runtime, history)
    assert mock_anthropic_messages.calls == []


async def test_model_switch_rechecks_budget_before_next_call(mock_anthropic_messages):
    selections = []

    class Router:
        async def route(self, context):
            selections.append(context.turn)
            return "main" if len(selections) == 1 else "small"

    mock_anthropic_messages.push(tool_use_response(tool_use_id="a", name="echo", input={"text": "x"}, model="main"))
    mock_anthropic_messages.push(text_response("short checkpoint", model="summary"))
    mock_anthropic_messages.push(text_response("done", model="small"))
    compaction = policy(trigger=TokenThreshold(tokens=90_000),
                        context_windows={"main": 100_000, "small": 1500, "summary": 100_000})
    options = LiteAgentOptions(model_router=Router(), tools=[EchoTool()], compaction=compaction, max_tokens=100)
    async with LiteAgentClient(options=options, history=old_history()) as agent:
        events = [event async for event in agent.query("current request")]
    completed = next(event for event in events if isinstance(event, CompactionCompleted))
    assert completed.reason == "budget" and completed.model == "small"
    assert completed.tokens_after <= 1400
    assert selections == [1, 1]
    assert [call["model"] for call in mock_anthropic_messages.calls] == ["main", "summary", "small"]


async def test_no_reduction_for_oversized_input_never_calls_main_model(mock_anthropic_messages):
    options = LiteAgentOptions(model="main", max_tokens=100,
                               compaction=policy(context_windows={"main": 1500, "summary": 100_000}))
    events = []
    with pytest.raises(ContextBudgetExceeded):
        async for event in query(prompt="huge current request " * 1000, options=options):
            events.append(event)
    assert isinstance(events[-1], CompactionFailed)
    assert mock_anthropic_messages.calls == []


async def test_unknown_window_requires_metadata_for_fraction(monkeypatch, mock_anthropic_messages):
    monkeypatch.setattr("liteagents.compaction.litellm.get_model_info", lambda model: {})
    options = LiteAgentOptions(model="unknown", compaction=policy(trigger=TokenThreshold(fraction=0.8)))
    with pytest.raises(CompactionError, match="Unknown context window"):
        _ = [event async for event in query(prompt="hi", options=options)]
    assert not mock_anthropic_messages.calls


async def test_system_and_tool_schemas_are_in_counter_input(mock_anthropic_messages):
    texts = []

    def counter(model, text):
        texts.append(json.loads(text))
        return count_chars(model, text)

    mock_anthropic_messages.push(text_response("done", model="main"))
    options = LiteAgentOptions(model="main", system="special instructions", tools=[EchoTool()],
                               compaction=policy(token_counter=counter, trigger=TokenThreshold(tokens=90_000)))
    _ = [event async for event in query(prompt="hi", options=options)]
    request = texts[0]
    assert request["system"] == "special instructions"
    assert request["tools"][0]["name"] == "echo"
    assert request["messages"][0]["content"] == "hi"


async def test_custom_strategy_state_isolated_and_committed_atomically():
    class Strategy:
        async def compact(self, context):
            state = context.state or {"compactions": 0}
            state["compactions"] += 1
            # Mutation of the detached view must not affect the actual retained message.
            context.messages[-1].content = "plugin mutation"
            return CompactionResult(CompactionUpdate(len(context.messages), prefix=ReplacePrefix(2, "short")),
                                    state=state)

    shared = policy(strategy=Strategy())
    first, second = CompactionRuntime(shared), CompactionRuntime(shared)
    for runtime in (first, second):
        history = ConversationHistory([*old_history(), UserMessage("untouched")])
        await run(runtime, history)
        assert history.messages[-1].content == "untouched"
        assert runtime.state == {"compactions": 1}
    assert first.state is not second.state


async def test_stale_results_and_state_rejected():
    history = ConversationHistory([*old_history(), UserMessage("current")])

    class Strategy:
        async def compact(self, context):
            history.add_user_text("new arrival")
            return CompactionResult(CompactionUpdate(len(context.messages), prefix=ReplacePrefix(2, "short")),
                                    state={"must_not_commit": True})

    runtime = CompactionRuntime(policy(strategy=Strategy()))
    with pytest.raises(CompactionError, match="History changed"):
        await run(runtime, history)
    assert runtime.state is None
    assert history.messages[1].content[0].text == "old detail " * 500
    assert history.messages[-1].content == "new arrival"


async def test_cancellation_and_busy_guard_release_without_partial_commit():
    entered = asyncio.Event()

    class BlockingStrategy:
        async def compact(self, context):
            entered.set()
            await asyncio.Event().wait()

    options = LiteAgentOptions(model="main", compaction=policy(strategy=BlockingStrategy(), trigger=None))
    async with LiteAgentClient(options=options, history=old_history()) as agent:
        before = agent.history
        task = asyncio.create_task(agent.compact())
        await entered.wait()
        with pytest.raises(RuntimeError, match="already"):
            await agent.compact()
        with pytest.raises(RuntimeError, match="already"):
            _ = [event async for event in agent.query("overlap")]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert agent.history == before
        assert agent._busy is False


async def test_query_close_releases_busy_guard(mock_anthropic_messages):
    mock_anthropic_messages.push(text_response("answer", model="main"))
    mock_anthropic_messages.push(text_response("next", model="main"))
    async with LiteAgentClient(options=LiteAgentOptions(model="main")) as agent:
        async with aclosing(agent.query("first")) as stream:
            await anext(stream)
        assert agent._busy is False
        _ = [event async for event in agent.query("second")]


async def test_fusion_has_independent_compaction_runtime(mock_anthropic_messages):
    shared = policy(strategy=PruneToolResults(keep=0), trigger=TokenThreshold(tokens=100))
    options = LiteAgentOptions(model="main", compaction=shared,
                               fusion=FusionOptions(sidekick_model="sidekick", sidekick_compaction=shared))
    mock_anthropic_messages.push(tool_use_response(tool_use_id="d", name="delegate_to_sidekick", input={"task": "check"}, model="main"))
    mock_anthropic_messages.push(text_response("checked", model="sidekick"))
    mock_anthropic_messages.push(text_response("done", model="main"))
    async with LiteAgentClient(options=options) as agent:
        _ = [event async for event in agent.query("check")]
        assert agent._compaction is not agent._fusion._compaction
        assert agent._compaction.last_model == "main"
        assert agent._fusion._compaction.last_model == "sidekick"


@pytest.mark.parametrize("factory,kwargs", [
    pytest.param(TokenThreshold, {}, id="missing-threshold"),
    pytest.param(TokenThreshold, {"tokens": 1, "fraction": 0.8}, id="ambiguous-threshold"),
    pytest.param(TokenThreshold, {"tokens": 0}, id="zero-threshold"),
    pytest.param(TokenThreshold, {"fraction": 1}, id="fraction-at-capacity"),
    pytest.param(TokenThreshold, {"fraction": float("nan")}, id="nan-fraction"),
    pytest.param(RecentTokens, {"tokens": 0}, id="zero-retention"),
    pytest.param(PruneToolResults, {"keep": -1}, id="negative-result-retention"),
    pytest.param(Summarize, {"max_tokens": 0}, id="zero-summary-budget"),
    pytest.param(Summarize, {"model_kwargs": {"tools": []}}, id="reserved-request-field"),
    pytest.param(policy, {"safety_margin": -1}, id="negative-safety-margin"),
    pytest.param(policy, {"context_windows": {"main": 0}}, id="zero-context-window"),
    pytest.param(TokenEstimate, {"tokens": -1, "source": "bad"}, id="negative-token-estimate"),
])
def test_invalid_configuration(factory, kwargs):
    with pytest.raises(ValueError):
        factory(**kwargs)


async def test_previous_summary_is_folded_into_next_summary(mock_anthropic_messages):
    mock_anthropic_messages.push(text_response("initial checkpoint", model="summary"))
    mock_anthropic_messages.push(text_response("updated checkpoint", model="summary"))
    runtime = CompactionRuntime(policy())
    history = ConversationHistory([*old_history(), UserMessage("current")])
    await run(runtime, history)
    history.add_assistant_response([{"type": "text", "text": "more detail " * 500}], "main")
    history.add_user_text("follow-up")
    await run(runtime, history)
    assert "initial checkpoint" in mock_anthropic_messages.calls[1]["messages"][0]["content"]
    assert sum(isinstance(message, SummaryMessage) for message in history.messages) == 1


async def test_pending_tool_calls_cannot_be_compacted(mock_anthropic_messages):
    history = ConversationHistory([UserMessage("goal"), pair("pending", "unused")[0]])
    with pytest.raises(CompactionError, match="outstanding"):
        await run(CompactionRuntime(policy()), history, manual=True)
    assert mock_anthropic_messages.calls == []


async def test_streaming_does_not_emit_summary_as_answer(monkeypatch):
    stream = Stream(response_events(text="final answer"))
    calls = []

    async def request(**kwargs):
        calls.append(kwargs)
        if kwargs["model"] == "summary":
            return text_response("context only", model="summary")
        return stream

    monkeypatch.setattr("litellm.anthropic_messages", request)
    options = LiteAgentOptions(model="main", stream=True, compaction=policy())
    events = [event async for event in query(prompt="current", options=options, history=old_history())]
    assert isinstance(events[0], CompactionStarted)
    assert isinstance(events[1], CompactionCompleted)
    assert "".join(event.text for event in events if isinstance(event, TextDelta)) == "final answer"
    assert calls[0]["stream"] is False and calls[1]["stream"] is True
    assert stream.closed


async def test_event_mutation_cannot_change_tool_execution_or_history(mock_anthropic_messages):
    mock_anthropic_messages.push(tool_use_response(tool_use_id="a", name="echo", input={"text": "original"}, model="main"))
    mock_anthropic_messages.push(text_response("done", model="main"))
    options = LiteAgentOptions(model="main", tools=[EchoTool()])
    async with LiteAgentClient(options=options) as agent:
        async with aclosing(agent.query("goal")) as stream:
            event = await anext(stream)
            event.content[0].input["text"] = "changed by observer"
            rest = [item async for item in stream]
        assert rest[0].content[0].content == "echoed: original"
        rest[0].content[0].content = "another mutation"
        assert agent.history[2].content[0].content == "echoed: original"
    assert mock_anthropic_messages.calls[1]["messages"][1]["content"][0]["input"] == {"text": "original"}


async def test_custom_failure_does_not_commit_mutated_state():
    class Strategy:
        async def compact(self, context):
            context.state["counter"] += 1
            raise RuntimeError("strategy failed")

    runtime = CompactionRuntime(policy(strategy=Strategy()))
    runtime.state = {"counter": 1}
    history = ConversationHistory([*old_history(), UserMessage("current")])
    before = deepcopy(history.raw())
    with pytest.raises(CompactionError, match="strategy failed"):
        await run(runtime, history)
    assert runtime.state == {"counter": 1}
    assert history.raw() == before


async def test_manual_only_oversized_context_does_not_automatically_compact(mock_anthropic_messages):
    options = LiteAgentOptions(model="main", max_tokens=100,
                               compaction=policy(trigger=None, context_windows={"main": 500}))
    with pytest.raises(ContextBudgetExceeded, match="manually"):
        _ = [event async for event in query(prompt="x" * 1000, options=options)]
    assert not mock_anthropic_messages.calls


async def test_absolute_threshold_can_use_unknown_target_with_explicit_summary_window(monkeypatch, mock_anthropic_messages):
    monkeypatch.setattr("liteagents.compaction.litellm.get_model_info", lambda model: {})
    mock_anthropic_messages.push(text_response("summary", model="summary"))
    mock_anthropic_messages.push(text_response("done", model="unknown"))
    options = LiteAgentOptions(model="unknown", compaction=policy(context_windows={"summary": 100_000}))
    events = [event async for event in query(prompt="current", options=options, history=old_history())]
    assert isinstance(events[1], CompactionCompleted)


async def test_fraction_uses_selected_model_input_reservation(mock_anthropic_messages):
    contexts = []

    class Trigger:
        def should_compact(self, context):
            contexts.append(context)
            return TokenThreshold(fraction=0.8).should_compact(context)

    mock_anthropic_messages.push(text_response("done", model="main"))
    options = LiteAgentOptions(model="main", max_tokens=1000,
                               compaction=policy(trigger=Trigger(), safety_margin=500))
    _ = [event async for event in query(prompt="short", options=options)]
    assert contexts[0].input_budget == 98_500


async def test_summary_does_not_inherit_main_reasoning_or_output_constraints(mock_anthropic_messages):
    mock_anthropic_messages.push(text_response("checkpoint", model="summary"))
    mock_anthropic_messages.push(text_response("done", model="main"))
    kwargs = {"api_base": "https://gateway.invalid", "thinking": {"type": "enabled", "budget_tokens": 8000},
              "stop_sequences": ["stop"], "output_config": {"format": {"type": "json_schema"}}}
    options = LiteAgentOptions(model="main", compaction=policy(), model_kwargs=kwargs)
    _ = [event async for event in query(prompt="current", options=options, history=old_history())]
    summary_call, main_call = mock_anthropic_messages.calls
    assert summary_call["api_base"] == kwargs["api_base"]
    assert not {"thinking", "stop_sequences", "output_config"}.intersection(summary_call)
    assert all(main_call[key] == value for key, value in kwargs.items())
