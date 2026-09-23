"""Token accounting scenarios: measured input, local deltas, and invalidation."""

from copy import deepcopy
from dataclasses import replace

import litellm
import pytest

from liteagents import (
    CompactionCompleted,
    CompactionOptions,
    CompactionResult,
    CompactionSkipped,
    CompactionUpdate,
    LiteAgentClient,
    LiteAgentOptions,
    ReplacePrefix,
    TokenCountRequest,
    TokenEstimate,
    TokenThreshold,
    UserMessage,
    cascade,
    heuristic_tokens,
)
from liteagents._internal.compaction_runtime import CompactionRuntime
from liteagents._internal.context_tokens import ContextTokens, input_tokens
from liteagents.compaction.tokens import estimate_tokens
from liteagents.history import ConversationHistory

from .compaction_helpers import count_chars, old_history, policy, run
from .conftest import text_response
from .test_loop import EchoTool
from .test_streaming import Stream, response_events


@pytest.mark.parametrize("usage,expected", [
    pytest.param({"input_tokens": 100}, 100, id="uncached"),
    pytest.param({"input_tokens": 10, "cache_read_input_tokens": 80,
                  "cache_creation_input_tokens": 10}, 100, id="all-cache-categories"),
    pytest.param({"input_tokens": 0, "cache_read_input_tokens": 100}, 100, id="fully-cached"),
    pytest.param({"input_tokens": 10, "cache_creation_input_tokens": 90,
                  "cache_creation": {"ephemeral_5m_input_tokens": 90}}, 100, id="no-double-count"),
    pytest.param({"input_tokens": 100, "output_tokens": 900, "total_tokens": 1000},
                 100, id="output-is-not-replayed-input"),
    pytest.param({"input_tokens": 100, "cache_read_input_tokens": None}, 100, id="null-cache"),
    pytest.param(None, None, id="no-usage"),
    pytest.param({"output_tokens": 100}, None, id="missing-input"),
    pytest.param({"input_tokens": 0}, None, id="zero-is-unavailable"),
    pytest.param({"input_tokens": -1}, None, id="negative"),
    pytest.param({"input_tokens": True}, None, id="boolean"),
    pytest.param({"input_tokens": "100"}, None, id="string"),
    pytest.param({"input_tokens": 100, "cache_read_input_tokens": -1}, None, id="negative-cache"),
    pytest.param({"input_tokens": 100, "cache_read_input_tokens": False}, None, id="boolean-cache"),
])
def test_normalized_messages_usage(usage, expected):
    assert input_tokens(usage) == expected


@pytest.fixture
def measured_context():
    request = TokenCountRequest(({"role": "user", "content": "original input"},),
                                system="instructions", tools=({"name": "echo", "input_schema": {}},))
    meter = ContextTokens()
    meter.observe("main", request, {"tool_choice": None}, {"input_tokens": 5000}, "end_turn")
    return meter, request


def test_usage_anchor_counts_only_new_content_and_no_billed_reasoning(measured_context):
    meter, original = measured_context
    # Provider usage deliberately disagrees with our local counter.
    request = replace(original, messages=(*original.messages,
        {"role": "assistant", "content": [{"type": "text", "text": "answer"}]},
        {"role": "user", "content": "next"},
    ))
    local_delta = count_chars("main", request).tokens - count_chars("main", original).tokens
    estimate = meter.count("main", request, {"tool_choice": None}, count_chars)
    assert estimate == TokenEstimate(5000 + local_delta, "response_usage+test_chars")
    assert meter.count("main", original, {"tool_choice": None}, count_chars).tokens == 5000


@pytest.mark.parametrize("change", ["model", "system", "tools", "prefix", "settings", "shorter"])
def test_changed_request_invalidates_usage_and_cannot_resurrect_it(measured_context, change):
    meter, original = measured_context
    model, request, settings = "main", original, {"tool_choice": None}
    if change == "model":
        model = "other"
    elif change == "system":
        request = replace(original, system="new instructions")
    elif change == "tools":
        request = replace(original, tools=())
    elif change == "prefix":
        request = replace(original, messages=({"role": "user", "content": "edited"},))
    elif change == "settings":
        settings = {"tool_choice": {"type": "auto"}}
    else:
        request = replace(original, messages=())
    assert meter.count(model, request, settings, count_chars) == count_chars(model, request)
    assert meter.count("main", original, {"tool_choice": None}, count_chars) == count_chars("main", original)


@pytest.mark.parametrize("usage,stop", [
    pytest.param(None, "end_turn", id="missing-usage"),
    pytest.param({"input_tokens": 999}, "error", id="failed-response"),
    pytest.param({"input_tokens": 999}, "aborted", id="aborted-response"),
    pytest.param({"input_tokens": 999}, None, id="incomplete-response"),
])
def test_unusable_response_discards_previous_anchor(measured_context, usage, stop):
    meter, request = measured_context
    meter.observe("main", request, {}, usage, stop)
    assert meter.count("main", request, {}, count_chars) == count_chars("main", request)


def test_anchor_and_counter_inputs_are_detached(measured_context):
    meter, request = measured_context
    request.messages[0]["content"] = "external edit"
    assert meter.request.messages[0]["content"] == "original input"
    snapshot = deepcopy(meter.request)

    def mutating_counter(model, candidate):
        candidate.messages[0]["content"] = "plugin edit"
        return TokenEstimate(10, "custom")

    meter.count("main", snapshot, {"tool_choice": None}, mutating_counter)
    assert meter.request.messages[0]["content"] == "original input"
    assert snapshot.messages[0]["content"] == "original input"


def test_default_counter_passes_structured_content_without_fetching_images(monkeypatch):
    request = TokenCountRequest((
        {"role": "assistant", "content": [{"type": "tool_use", "id": "a", "name": "echo", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": [
            {"type": "image", "source": {"type": "url", "url": "https://example.invalid/image.png"}},
        ]}]},
    ), system="system", tools=({"name": "echo", "input_schema": {"type": "object"}},))
    captured = []

    def counter(**kwargs):
        captured.append(deepcopy(kwargs))
        kwargs["messages"].clear()
        return 123

    monkeypatch.setattr(litellm, "token_counter", counter)
    assert CompactionOptions().token_counter("main", request) == TokenEstimate(123, "litellm.token_counter")
    assert captured[0] == {
        "model": "main", "messages": [{"role": "system", "content": "system"}, *request.messages],
        "tools": list(request.tools), "use_default_image_token_count": True,
    }


@pytest.mark.parametrize("failure", ["disabled", "unsupported"])
def test_default_counter_never_silently_switches_to_byte_heuristic(monkeypatch, failure):
    request = TokenCountRequest(({"role": "user", "content": "hello"},))
    if failure == "disabled":
        monkeypatch.setattr(litellm, "disable_token_counter", True)
    else:
        def broken(**kwargs):
            raise ValueError("unsupported")
        monkeypatch.setattr(litellm, "token_counter", broken)
    with pytest.raises(ValueError):
        estimate_tokens("unknown", request)
    assert heuristic_tokens("unknown", request).source == "utf8_bytes/3"
    assert heuristic_tokens("unknown", request).tokens > 0


def test_real_litellm_counter_accounts_for_tools_results_and_unicode():
    # Exercise the installed tokenizer, not a mocked counter or a live model.
    request = TokenCountRequest(({"role": "user", "content": "héllo 世界"},))
    base = estimate_tokens("gpt-4o", request).tokens
    tool = EchoTool().to_anthropic_tool()
    with_tools = replace(request, system="Follow instructions", tools=(tool,))
    assert estimate_tokens("gpt-4o", with_tools).tokens > base > 0
    tool_round = replace(with_tools, messages=(*with_tools.messages,
        {"role": "assistant", "content": [{"type": "tool_use", "id": "a", "name": "echo", "input": {"text": "hi"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": "detailed results " * 100}]},
    ))
    assert estimate_tokens("gpt-4o", tool_round).tokens > estimate_tokens("gpt-4o", with_tools).tokens


@pytest.mark.parametrize("streaming", [False, True], ids=["buffered", "streamed"])
async def test_response_usage_drives_next_turn_threshold(monkeypatch, streaming):
    observed, calls = [], []

    class Inspect:
        async def compact(self, context):
            observed.append(context.tokens)

    async def provider(**kwargs):
        calls.append(kwargs)
        # Cached input alone crosses the threshold; output is deliberately huge.
        usage = {"input_tokens": 100, "cache_read_input_tokens": 9000,
                 "cache_creation_input_tokens": 100, "output_tokens": 999999}
        if streaming:
            events = response_events(text="answer")
            events[0]["message"]["usage"] = usage | {"output_tokens": 0}
            events[-2]["usage"] = {"output_tokens": 999999}
            return Stream(events)
        return text_response("answer", model="main") | {"usage": usage}

    monkeypatch.setattr(litellm, "anthropic_messages", provider)
    options = LiteAgentOptions(model="main", stream=streaming, max_tokens=100,
        compaction=policy(strategy=Inspect(), trigger=TokenThreshold(tokens=5000)))
    async with LiteAgentClient(options=options) as agent:
        _ = [event async for event in agent.query("hello")]
        assert not observed
        _ = [event async for event in agent.query("continue")]
        _ = [event async for event in agent.query("continue again")]
    original = TokenCountRequest(tuple(calls[0]["messages"]))
    current = TokenCountRequest(tuple(calls[1]["messages"]))
    delta = count_chars("main", current).tokens - count_chars("main", original).tokens
    assert observed[0] == TokenEstimate(9200 + delta, "response_usage+test_chars")
    third = TokenCountRequest(tuple(calls[2]["messages"]))
    latest_delta = count_chars("main", third).tokens - count_chars("main", current).tokens
    assert observed[1] == TokenEstimate(9200 + latest_delta, "response_usage+test_chars")


@pytest.mark.parametrize("composition", [False, True], ids=["single", "cascade"])
@pytest.mark.parametrize("change", ["shrink", "grow", "noop"])
async def test_previews_use_local_counts_and_only_real_reductions_commit(composition, change):
    observed = []

    class Edit:
        async def compact(self, context):
            update = CompactionUpdate(len(context.messages))
            if change != "noop":
                update = replace(update, prefix=ReplacePrefix(2, "summary" if change == "shrink" else "x" * 20000))
            observed.append(context.preview(update).tokens)
            return CompactionResult(update)

    strategy = cascade([Edit()]) if composition else Edit()
    runtime = CompactionRuntime(policy(strategy=strategy, target_tokens=1))
    history = ConversationHistory([*old_history(), UserMessage("current request")])
    original = history.snapshot()
    request = TokenCountRequest(tuple(deepcopy(history.raw())), system="instructions")
    # A large usage/local discrepancy must never make a no-op or growing edit
    # look like a reduction. A shrinking edit must drop the old usage anchor.
    runtime.context_tokens.observe("main", request, {"tool_choice": None},
                                    {"input_tokens": 90000}, "end_turn")
    events = await run(runtime, history, manual=True)
    assert observed[0].source == "test_chars"
    if change == "shrink":
        assert isinstance(events[-1], CompactionCompleted)
        assert events[-1].tokens_before == 90000
        assert events[-1].tokens_after == observed[0].tokens
        assert events[-1].token_source_after == "test_chars"
        assert runtime.context_tokens.request is None
        after = await run(runtime, history, manual=True)
        assert after[0].token_source == "test_chars"
    else:
        assert isinstance(events[-1], CompactionSkipped)
        assert history.raw() == original.raw()
        assert runtime.context_tokens.request == request


async def test_imported_history_usage_is_not_a_verified_baseline():
    observed = []

    class Inspect:
        async def compact(self, context):
            observed.append(context.tokens)

    messages = old_history()
    messages[-1].usage = {"input_tokens": 90000}
    history = ConversationHistory(messages)
    runtime = CompactionRuntime(policy(strategy=Inspect()))
    await run(runtime, history, manual=True)
    assert observed == [count_chars("main", TokenCountRequest(tuple(history.raw()), "instructions"))]


@pytest.mark.parametrize("automatic", [False, True], ids=["manual-only", "automatic"])
async def test_reported_usage_enforces_hard_budget_even_when_local_estimate_fits(automatic):
    from liteagents import ContextBudgetExceeded

    class NothingToReduce:
        async def compact(self, context):
            assert context.reason == "budget"

    history = ConversationHistory([UserMessage("small visible input")])
    request = TokenCountRequest(tuple(history.raw()), "instructions")
    runtime = CompactionRuntime(policy(
        strategy=NothingToReduce(), trigger=TokenThreshold(tokens=99999) if automatic else None,
        context_windows={"main": 1000},
    ))
    runtime.context_tokens.observe("main", request, {"tool_choice": None}, {"input_tokens": 10000}, "end_turn")
    with pytest.raises(ContextBudgetExceeded):
        await run(runtime, history)
