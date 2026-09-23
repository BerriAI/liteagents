"""Scheduling, coverage, recovery and isolation of asynchronous context management."""

import asyncio
import json
from contextlib import aclosing

import pytest

from liteagents import (
    AssistantMessage,
    BackgroundMemoryOptions,
    CompactionCompleted,
    CompactionError,
    CompactionFailed,
    ContextBudgetExceeded,
    LiteAgentClient,
    LiteAgentOptions,
    SummaryMessage,
    Tool,
    ToolResultBlock,
    UserMessage,
)
from liteagents._internal.memory_tools import ReadHistory, SearchHistory

from .compaction_helpers import count_chars, replay_events
from .conftest import text_response, tool_use_response
from .test_loop import EchoTool


def memory_options(**kwargs):
    return BackgroundMemoryOptions(**({
        "model": "memory", "max_recent_turns": 3, "max_context_tokens": 30_000,
        "max_memory_tokens": 1000, "max_observation_tokens": 15_000,
        "min_observation_tokens": 1,
        "token_counter": count_chars, "context_windows": {"main": 100_000, "memory": 100_000},
        "safety_margin": 0,
    } | kwargs))


class Provider:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.calls = []
        self.error = None
        self.notes = "Objective: continue. Constraints: preserve requirements. [message:1]"

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["model"] == "memory":
            self.started.set()
            try:
                await self.release.wait()
                if self.error:
                    raise self.error
                return text_response(self.notes, model="memory")
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
            finally:
                self.finished.set()
        return text_response("Detailed answer " * 100, model="main")

    async def ready(self):
        self.release.set()
        await asyncio.wait_for(self.finished.wait(), 1)
        # The wait_for task around the provider has to publish its completion too.
        await asyncio.sleep(0)
        await asyncio.sleep(0)


@pytest.fixture
def provider(monkeypatch):
    result = Provider()
    monkeypatch.setattr("litellm.anthropic_messages", result)
    return result


async def collect(client, prompt):
    return [event async for event in client.query(prompt)]


async def test_main_continues_while_memory_runs_and_waits_only_at_turn_cap(provider):
    async with LiteAgentClient(options=LiteAgentOptions(
        model="main", compaction=memory_options(), max_tokens=100,
    )) as client:
        await collect(client, "first")
        await asyncio.wait_for(provider.started.wait(), 1)
        await asyncio.wait_for(collect(client, "second"), 1)
        await asyncio.wait_for(collect(client, "third"), 1)
        assert client.memory.version == 0
        fourth = asyncio.create_task(collect(client, "fourth"))
        await asyncio.sleep(0)
        assert not fourth.done()
        assert len([c for c in provider.calls if c["model"] == "main"]) == 3
        await provider.ready()
        events = await asyncio.wait_for(fourth, 1)
        assert any(isinstance(e, CompactionCompleted) for e in events)
        assert [m.content for m in client.transcript if isinstance(m, UserMessage)] == [
            "first", "second", "third", "fourth",
        ]
        assert client.memory.processed_through >= 2
        sent = [c for c in provider.calls if c["model"] == "main"][-1]["messages"]
        assert sent[-1]["content"] == "fourth"
        assert sum(isinstance(m["content"], str) and m["content"] in ["first", "second", "third", "fourth"]
                   for m in sent) <= 3


async def test_publication_preserves_appended_tail_and_replayable_events(provider):
    async with LiteAgentClient(options=LiteAgentOptions(model="main", compaction=memory_options())) as client:
        mirror = [UserMessage("original requirement")]
        first = await collect(client, "original requirement")
        mirror = replay_events(mirror, first)
        await provider.ready()
        mirror.append(UserMessage("correction: use the new requirement"))
        events = await collect(client, "correction: use the new requirement")
        assert replay_events(mirror, events) == client.history
        assert len(client.transcript) == 4
        assert client.transcript[0].content == "original requirement"
        assert client.memory.processed_through == 2
        sent = [c for c in provider.calls if c["model"] == "main"][-1]["messages"]
        assert sent[-1]["content"] == "correction: use the new requirement"
        assert isinstance(client.history[0], SummaryMessage)
        copy = client.transcript
        copy[0].content = "mutated"
        assert client.transcript[0].content == "original requirement"


async def test_observer_consumes_only_new_events_after_published_cursor(provider):
    async with LiteAgentClient(options=LiteAgentOptions(model="main", compaction=memory_options())) as client:
        await collect(client, "first")
        await provider.ready()
        await collect(client, "second")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        observations = [json.loads(c["messages"][0]["content"]) for c in provider.calls if c["model"] == "memory"]
        assert len(observations) == 2
        assert [e["id"] for e in observations[0]["events"]] == [1, 2]
        assert [e["id"] for e in observations[1]["events"]] == [3, 4]
        assert observations[1]["previous_notes"] == provider.notes


async def test_failed_observer_preserves_history_and_blocks_oversized_request(provider):
    provider.error = ConnectionError("observer unavailable")
    async with LiteAgentClient(options=LiteAgentOptions(
        model="main", compaction=memory_options(max_recent_turns=1),
    )) as client:
        await collect(client, "first")
        await provider.ready()
        with pytest.raises(ContextBudgetExceeded, match="original history is intact"):
            await collect(client, "second")
        assert client.memory.version == 0
        assert client.history == client.transcript
        assert len([c for c in provider.calls if c["model"] == "main"]) == 1
        provider.error = None
        events = await collect(client, "third")
        assert any(isinstance(e, CompactionCompleted) for e in events)
        assert [m.content for m in client.transcript if isinstance(m, UserMessage)] == ["first", "second", "third"]


async def test_failure_below_cap_emits_event_and_continues(provider):
    provider.error = ValueError("bad response")
    async with LiteAgentClient(options=LiteAgentOptions(model="main", compaction=memory_options())) as client:
        await collect(client, "first")
        await provider.ready()
        events = await collect(client, "second")
        assert any(isinstance(e, CompactionFailed) for e in events)
        assert isinstance(events[-1], AssistantMessage)
        assert client.history == client.transcript


async def test_close_cancels_idle_observer(provider):
    async with LiteAgentClient(options=LiteAgentOptions(model="main", compaction=memory_options())) as client:
        await collect(client, "first")
        await asyncio.wait_for(provider.started.wait(), 1)
    assert provider.cancelled.is_set()


async def test_closing_query_early_cancels_background_work(provider):
    async with LiteAgentClient(options=LiteAgentOptions(model="main", compaction=memory_options())) as client:
        async with aclosing(client.query("first")) as events:
            await anext(events)  # background start event, before the assistant response
            await asyncio.wait_for(provider.started.wait(), 1)
        assert provider.cancelled.is_set()
        assert client.memory.version == 0


async def test_timeout_does_not_send_over_limit_context(provider):
    async with LiteAgentClient(options=LiteAgentOptions(
        model="main", compaction=memory_options(timeout=0.01, max_recent_turns=1),
    )) as client:
        await collect(client, "first")
        with pytest.raises(ContextBudgetExceeded):
            await collect(client, "second")
        assert provider.cancelled.is_set()
        assert len([c for c in provider.calls if c["model"] == "main"]) == 1


async def test_oversized_current_request_fails_without_provider_call(provider):
    async with LiteAgentClient(options=LiteAgentOptions(
        model="main", compaction=memory_options(max_context_tokens=4000),
    )) as client:
        with pytest.raises(ContextBudgetExceeded, match="current request"):
            await collect(client, "x" * 5000)
        assert provider.calls == []
        assert client.transcript == [UserMessage("x" * 5000)]


@pytest.mark.parametrize("notes", ["", "x" * 1001])
async def test_invalid_memory_cannot_advance_cursor(provider, notes):
    provider.notes = notes
    async with LiteAgentClient(options=LiteAgentOptions(
        model="main", compaction=memory_options(max_recent_turns=1),
    )) as client:
        await collect(client, "first")
        await provider.ready()
        with pytest.raises(ContextBudgetExceeded):
            await collect(client, "second")
        assert client.memory.processed_through == 0
        assert client.history == client.transcript


async def test_originals_can_be_searched_and_paged_after_eviction(provider):
    async with LiteAgentClient(options=LiteAgentOptions(model="main", compaction=memory_options())) as client:
        await collect(client, "Rare identifier: archive-719; " + "payload " * 800)
        await provider.ready()
        await collect(client, "continue")
        archive = client._compaction.archive
        search = json.loads(await SearchHistory(archive).execute({"query": "ARCHIVE-719"}))
        assert search["matches"][0]["message_id"] == 1
        first = json.loads(await ReadHistory(archive).execute({"message_id": 1, "max_chars": 50}))
        assert first["next_offset"] == 50
        second = json.loads(await ReadHistory(archive).execute({"message_id": 1, "offset": 50, "max_chars": 50}))
        assert first["text"] + second["text"] == json.dumps({"content": client.transcript[0].content}, ensure_ascii=False)[:100]
        assert "archive-719" not in str(client.history)


async def test_gateway_settings_inherited_with_separate_observer_generation_settings(provider):
    config = memory_options(model_kwargs={"api_key": "MEMORY_SECRET", "reasoning_effort": "low"})
    async with LiteAgentClient(options=LiteAgentOptions(
        model="main", compaction=config,
        model_kwargs={"api_key": "MAIN_SECRET", "api_base": "https://test.invalid", "thinking": {"budget_tokens": 5000}},
    )) as client:
        await collect(client, "first")
        await provider.ready()
        observation = next(c for c in provider.calls if c["model"] == "memory")
        assert observation["api_key"] == "MEMORY_SECRET"
        assert observation["api_base"] == "https://test.invalid"
        assert observation["reasoning_effort"] == "low"
        assert "thinking" not in observation
        assert observation["tools"] is None
        assert "MEMORY_SECRET" not in repr(config)


async def test_tool_loop_retains_complete_pairs_and_current_user_request(monkeypatch):
    count = 0
    async def provider(**kwargs):
        nonlocal count
        if kwargs["model"] == "memory":
            return text_response("Completed previous echo. [message:1]", model="memory")
        from .test_compaction_integration import assert_complete_tool_groups
        assert_complete_tool_groups(kwargs["messages"])
        assert any(m["content"] == "run the sequence" for m in kwargs["messages"])
        count += 1
        if count == 7:
            return text_response("done", model="main")
        await asyncio.sleep(0)
        return tool_use_response(tool_use_id=f"call-{count}", name="echo", input={"text": "payload " * 80}, model="main")

    monkeypatch.setattr("litellm.anthropic_messages", provider)
    async with LiteAgentClient(options=LiteAgentOptions(
        model="main", tools=[EchoTool()], max_tokens=100,
        compaction=memory_options(max_context_tokens=6500),
    )) as client:
        events = await collect(client, "run the sequence")
        assert events[-1].content[0].text == "done"
        assert any(isinstance(e, CompactionCompleted) for e in events)
        assert replay_events([UserMessage("run the sequence")], events) == client.history
        assert len(client.transcript) == 14


@pytest.mark.parametrize("kwargs", [{"max_recent_turns": 0}, {"max_context_tokens": True},
                                    {"timeout": float("inf")}, {"timeout": 0}, {"model": " "},
                                    {"model_kwargs": {"messages": []}}])
def test_invalid_config_rejected(kwargs):
    with pytest.raises(ValueError):
        memory_options(**kwargs)


async def test_interruption_before_tool_dispatch_allows_next_user_turn(monkeypatch):
    calls = []
    async def provider(**kwargs):
        if kwargs["model"] == "memory":
            return text_response("User interrupted the pending action.", model="memory")
        calls.append(kwargs)
        if len(calls) == 1:
            return tool_use_response(tool_use_id="pending", name="echo", input={"text": "old"}, model="main")
        from liteagents.history import safe_boundaries
        safe_boundaries(client.history)
        return text_response("Followed the correction", model="main")

    monkeypatch.setattr("litellm.anthropic_messages", provider)
    async with LiteAgentClient(options=LiteAgentOptions(
        model="main", tools=[EchoTool()], compaction=memory_options(),
    )) as client:
        async with aclosing(client.query("old action")) as events:
            assert isinstance(await anext(events), AssistantMessage)
        result = client.history[-1].content[0]
        assert isinstance(result, ToolResultBlock)
        assert result.tool_use_id == "pending" and result.is_error
        assert "unknown" in result.content
        events = await collect(client, "Cancel that action; follow this correction")
        assert events[-1].content[0].text == "Followed the correction"


async def test_cancelled_batch_retains_completed_results_and_never_reexecutes(monkeypatch):
    entered = asyncio.Event()
    executed = []
    class Action(Tool):
        name, description, input_schema = "action", "fixture", {"type": "object"}
        async def execute(self, input):
            executed.append(input["n"])
            if input["n"] == 2:
                entered.set()
                await asyncio.Event().wait()
            return f"completed {input['n']}"

    main_calls = 0
    async def provider(**kwargs):
        nonlocal main_calls
        if kwargs["model"] == "memory":
            return text_response("First action done; second outcome uncertain.", model="memory")
        main_calls += 1
        if main_calls == 1:
            return {"content": [{"type": "tool_use", "id": str(i), "name": "action", "input": {"n": i}}
                                for i in [1, 2, 3]], "model": "main", "stop_reason": "tool_use"}
        return text_response("Stopped safely", model="main")

    monkeypatch.setattr("litellm.anthropic_messages", provider)
    async with LiteAgentClient(options=LiteAgentOptions(
        model="main", tools=[Action()], compaction=memory_options(),
    )) as client:
        task = asyncio.create_task(collect(client, "do three actions"))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        results = client.history[-1].content
        assert [r.tool_use_id for r in results] == ["1", "2", "3"]
        assert results[0].content == "completed 1"
        assert results[1].is_error and results[2].is_error
        await collect(client, "Stop; do not retry any action")
        assert executed == [1, 2]
        assert client.transcript[-3].content[0].content == "completed 1"


async def test_shared_options_have_independent_archives_and_cursors(provider):
    options = LiteAgentOptions(model="main", compaction=memory_options())
    async with LiteAgentClient(options=options) as first, LiteAgentClient(options=options) as second:
        await collect(first, "first client's private context")
        await provider.ready()
        await collect(first, "continue first")
        await collect(second, "second client's separate context")
        assert first.memory.version == 1
        assert second.memory.version == 0
        assert "first client's" not in str(second.transcript)
        assert "second client's" not in str(first.transcript)


async def test_large_old_tool_group_requires_larger_observation_budget(provider):
    from .compaction_helpers import pair
    history = pair("large", "payload " * 3000)
    async with LiteAgentClient(options=LiteAgentOptions(
        model="main", compaction=memory_options(max_context_tokens=4000, max_observation_tokens=2000),
    ), history=history) as client:
        with pytest.raises(ContextBudgetExceeded, match="observation budget"):
            await collect(client, "new")
        assert client.memory.version == 0
        assert client.transcript[:2] == history
        assert provider.calls == []


async def test_raw_provider_blocks_survive_replacement(provider):
    async with LiteAgentClient(options=LiteAgentOptions(model="main", compaction=memory_options())) as client:
        await collect(client, "first")
        await provider.ready()
        # Simulate a provider-only block on an unprocessed tail entry.
        client._history.add_user_text("second")
        client._history.add_assistant_response([
            {"type": "thinking", "thinking": "provider data", "signature": "signature"},
            {"type": "text", "text": "second answer"},
        ], "main")
        await collect(client, "third")
        outgoing = [c for c in provider.calls if c["model"] == "main"][-1]["messages"]
        assert any(isinstance(m["content"], list) and m["content"][0].get("type") == "thinking" for m in outgoing)
        assert any(isinstance(m["content"], list) and m["content"][0].get("type") == "thinking"
                   for m in client._compaction.archive.raw())


async def test_small_conversation_has_no_observer_or_recovery_tool_overhead(monkeypatch):
    requests = []
    async def provider(**kwargs):
        assert kwargs["model"] == "main"
        assert kwargs["tools"] is None
        requests.append(kwargs)
        return text_response("OK", model="main")

    monkeypatch.setattr("litellm.anthropic_messages", provider)
    async with LiteAgentClient(options=LiteAgentOptions(
        model="main", compaction=memory_options(max_recent_turns=None, min_observation_tokens=2000),
    )) as client:
        for i in range(8):
            await collect(client, f"Short message {i}")
        assert client.memory.version == 0
        assert client.history == client.transcript
    assert len(requests) == 8


async def test_latest_large_tool_result_can_be_observed_instead_of_overflowing(monkeypatch):
    executed = []
    class LargeResult(EchoTool):
        async def execute(self, input):
            executed.append(input)
            return "Result: completed, reference=key-71. " + "raw evidence " * 450

    main_calls = 0
    async def provider(**kwargs):
        nonlocal main_calls
        if kwargs["model"] == "memory":
            return text_response("The tool completed; reference=key-71. [message:3]", model="memory")
        main_calls += 1
        assert len(json.dumps({k: kwargs[k] for k in ["messages", "system", "tools"]})) <= 4000
        if main_calls == 1:
            return tool_use_response(tool_use_id="big", name="echo", input={"text": "go"}, model="main")
        assert "key-71" in json.dumps(kwargs["messages"])
        assert "raw evidence" not in json.dumps(kwargs["messages"])
        assert any(isinstance(m["content"], list) and any(
            b.get("type") == "tool_result" and "already returned" in b.get("content", "")
            for b in m["content"]) for m in kwargs["messages"])
        return text_response("Completed once", model="main")

    monkeypatch.setattr("litellm.anthropic_messages", provider)
    async with LiteAgentClient(options=LiteAgentOptions(
        model="main", tools=[LargeResult()], compaction=memory_options(max_context_tokens=4000),
    )) as client:
        events = await collect(client, "run once")
        assert events[-1].content[0].text == "Completed once"
        assert len(executed) == 1
        assert "raw evidence" in str(client.transcript)
        assert replay_events([UserMessage("run once")], events) == client.history


async def test_oversized_notes_retry_original_events_once_and_report_both_costs(monkeypatch):
    observation_requests = []
    async def provider(**kwargs):
        if kwargs["model"] == "memory":
            observation_requests.append(kwargs)
            return text_response("x" * 1001 if len(observation_requests) == 1 else "Concise notes [message:1]", model="memory")
        return text_response("answer " * 300, model="main")

    monkeypatch.setattr("litellm.anthropic_messages", provider)
    async with LiteAgentClient(options=LiteAgentOptions(
        model="main", compaction=memory_options(max_recent_turns=1),
    )) as client:
        await collect(client, "first")
        events = await collect(client, "second")
        completed = next(e for e in events if isinstance(e, CompactionCompleted))
        assert completed.usage.input_tokens == 2
        assert len(completed.usage.stages) == 2
        assert observation_requests[0]["messages"] == observation_requests[1]["messages"]
        assert "budget" in observation_requests[1]["system"]
        assert client.memory.processed_through == 2


async def test_malformed_initial_tool_history_never_reaches_provider(provider):
    from liteagents import ToolUseBlock
    history = [UserMessage("old task"), AssistantMessage([ToolUseBlock("pending", "echo", {})], "main")]
    async with LiteAgentClient(options=LiteAgentOptions(model="main", compaction=memory_options()), history=history) as client:
        with pytest.raises(CompactionError, match="Tool calls"):
            await collect(client, "continue")
        assert provider.calls == []


async def test_hundred_turns_have_bounded_requests_and_contiguous_archive_coverage(monkeypatch):
    observed = []
    sizes = []
    async def provider(**kwargs):
        if kwargs["model"] == "memory":
            observed.extend(e["id"] for e in json.loads(kwargs["messages"][0]["content"])["events"])
            return text_response("Continue the task; consult original evidence. [message:1]", model="memory")
        sizes.append(len(json.dumps({k: kwargs[k] for k in ["messages", "system", "tools"]}, ensure_ascii=False)))
        await asyncio.sleep(0)
        return text_response("completed details " * 50, model="main")

    monkeypatch.setattr("litellm.anthropic_messages", provider)
    async with LiteAgentClient(options=LiteAgentOptions(
        model="main", compaction=memory_options(max_context_tokens=4000),
    )) as client:
        for i in range(100):
            await collect(client, f"Continue step {i}")
        await client.compact()
        assert max(sizes) <= 4000
        assert len(client.transcript) == 200
        assert observed == list(range(1, len(observed) + 1))
        assert client.memory.processed_through == len(observed)


async def test_new_request_releases_already_covered_idle_pinned_prompt(provider):
    async with LiteAgentClient(options=LiteAgentOptions(
        model="main", compaction=memory_options(max_recent_turns=1),
    )) as client:
        first_events = await collect(client, "first")
        await provider.ready()
        compacted = await client.compact()
        assert client.memory.processed_through == 2
        assert any(m.content == "first" for m in client.history)
        mirror = replay_events([UserMessage("first")], first_events)
        mirror = replay_events(mirror, [compacted])
        mirror.append(UserMessage("second"))
        events = await collect(client, "second")
        assert not any(m.content == "first" for m in client.history)
        assert replay_events(mirror, events) == client.history
        assert [m.content for m in client.transcript if isinstance(m, UserMessage)] == ["first", "second"]


async def test_search_excludes_recovery_copies_but_exact_reads_retain_them():
    from liteagents.history import ConversationHistory
    archive = ConversationHistory([UserMessage("original needle-23")])
    archive.add_assistant_response([
        {"type": "tool_use", "id": "lookup", "name": "memory_search_history", "input": {"query": "needle-23"}},
    ], "main")
    archive.add_user_tool_results([
        {"type": "tool_result", "tool_use_id": "lookup", "content": "copy of needle-23"},
    ])
    result = json.loads(await SearchHistory(archive).execute({"query": "needle-23"}))
    assert [m["message_id"] for m in result["matches"]] == [1]
    exact = json.loads(await ReadHistory(archive).execute({"message_id": 3}))
    assert "copy of needle-23" in exact["text"]


async def test_manual_compaction_returns_replayable_batch_for_multiple_observations(monkeypatch):
    from liteagents import TextBlock
    async def provider(**kwargs):
        assert kwargs["model"] == "memory"
        return text_response("Concise working state", model="memory")

    monkeypatch.setattr("litellm.anthropic_messages", provider)
    history = [item for i in range(5) for item in (
        UserMessage(f"instruction {i} " + "x" * 700),
        AssistantMessage([TextBlock("answer " * 100)], "main"),
    )]
    async with LiteAgentClient(options=LiteAgentOptions(
        model="main", compaction=memory_options(max_context_tokens=3500, max_observation_tokens=3200),
    ), history=history) as client:
        result = await client.compact()
        assert client.memory.version > 1
        assert replay_events(history, [result]) == client.history


async def test_output_truncation_gets_one_same_source_repair_with_usage(monkeypatch):
    requests = []
    async def provider(**kwargs):
        if kwargs["model"] == "memory":
            requests.append(kwargs)
            response = text_response("partial state" if len(requests) == 1 else "Verified current state", model="memory")
            if len(requests) == 1:
                response["stop_reason"] = "max_tokens"
            return response
        return text_response("answer " * 200, model="main")

    monkeypatch.setattr("litellm.anthropic_messages", provider)
    async with LiteAgentClient(options=LiteAgentOptions(
        model="main", compaction=memory_options(max_recent_turns=1),
    )) as client:
        await collect(client, "first")
        events = await collect(client, "second")
        result = next(e for e in events if isinstance(e, CompactionCompleted))
        assert len(result.usage.stages) == 2
        assert requests[0]["messages"] == requests[1]["messages"]
        assert "incomplete" in requests[1]["system"]
        assert client.memory.notes == "Verified current state"


async def test_fusion_observer_is_independent_and_closed_with_runtime(provider):
    from liteagents import FusionOptions
    from liteagents.fusion import FusionRuntime
    runtime = FusionRuntime(main_tools=[], options=FusionOptions(
        sidekick_model="sidekick", sidekick_compaction=memory_options(),
    ))
    answer = await runtime.delegate("independent subtask")
    assert answer
    await asyncio.wait_for(provider.started.wait(), 1)
    assert runtime._compaction.archive.messages[0].content == "independent subtask"
    await runtime.close()
    assert provider.cancelled.is_set()


async def test_closing_streamed_followup_cancels_observer_and_provider(monkeypatch):
    from liteagents import TextDelta

    from .test_streaming import Stream, response_events
    started, cancelled = asyncio.Event(), asyncio.Event()
    streams = []
    async def provider(**kwargs):
        if kwargs["model"] == "memory":
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
        stream = Stream(response_events(text="response"))
        streams.append(stream)
        return stream

    monkeypatch.setattr("litellm.anthropic_messages", provider)
    async with LiteAgentClient(options=LiteAgentOptions(
        model="main", stream=True, compaction=memory_options(),
    )) as client:
        await collect(client, "first")
        await asyncio.wait_for(started.wait(), 1)
        async with aclosing(client.query("followup")) as events:
            async for event in events:
                if isinstance(event, TextDelta):
                    break
        assert cancelled.is_set()
        assert all(stream.closed for stream in streams)
        # Incomplete streamed messages are not committed, matching the SDK's
        # existing streaming contract. The incoming human request survives.
        assert client.transcript[-1] == UserMessage("followup")


async def test_manual_multi_observation_failure_rolls_back_and_accounts_for_completed_work(monkeypatch):
    from liteagents import TextBlock
    calls = 0
    async def provider(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ConnectionError("second chunk failed")
        return text_response("Current state", model="memory")

    monkeypatch.setattr("litellm.anthropic_messages", provider)
    history = [item for i in range(5) for item in (
        UserMessage(f"instruction {i} " + "x" * 700),
        AssistantMessage([TextBlock("answer " * 100)], "main"),
    )]
    async with LiteAgentClient(options=LiteAgentOptions(
        model="main", compaction=memory_options(max_context_tokens=3500, max_observation_tokens=3200),
    ), history=history) as client:
        with pytest.raises(ContextBudgetExceeded, match="second chunk failed") as error:
            await client.compact()
        assert client.history == client.transcript == history
        assert client.memory.version == 0
        assert error.value.usage.input_tokens == 1
        result = await client.compact()
        assert replay_events(history, [result]) == client.history
