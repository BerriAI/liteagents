"""Public configuration, ownership, and runtime capability scenarios."""

from copy import deepcopy

import pytest

from liteagents import (
    BatchUpdate,
    CompactionContext,
    CompactionOptions,
    CompactionResult,
    HistoryEdit,
    PruneToolResults,
    RecentTokens,
    ReplacePrefix,
    ReplaceToolResult,
    Summarize,
    TokenEstimate,
    TokenThreshold,
    TokenUsage,
    TurnThreshold,
    UserMessage,
    cascade,
)
from liteagents._internal.compaction_runtime import CompactionRuntime
from liteagents.history import ConversationHistory

from .compaction_helpers import count_chars, old_history, policy, run


@pytest.mark.parametrize("constructor,field", [
    pytest.param(TokenThreshold, "tokens", id="token-trigger"),
    pytest.param(TurnThreshold, "turns", id="turn-trigger"),
    pytest.param(RecentTokens, "tokens", id="retention"),
    pytest.param(Summarize, "max_tokens", id="summary-output"),
    pytest.param(PruneToolResults, "keep", id="pruning-retention"),
    pytest.param(CompactionOptions, "target_tokens", id="reduction-target"),
    pytest.param(CompactionOptions, "safety_margin", id="safety-margin"),
])
@pytest.mark.parametrize("value", [True, 1.5, float("nan"), float("inf"), "100"],
                         ids=["boolean", "float", "nan", "infinity", "string"])
def test_numeric_configuration_rejects_nonintegers(constructor, field, value):
    with pytest.raises(ValueError, match="integer"):
        constructor(**{field: value})


@pytest.mark.parametrize("value", [True, "0.8", float("nan"), float("inf"), 0, 1])
def test_fraction_requires_a_finite_number_in_range(value):
    with pytest.raises(ValueError, match="between"):
        TokenThreshold(fraction=value)


@pytest.mark.parametrize("value", [True, 1.5, float("nan"), -1, 0])
def test_model_windows_are_strict_positive_integers(value):
    with pytest.raises(ValueError):
        CompactionOptions(context_windows={"main": value})


@pytest.mark.parametrize("constructor,kwargs", [
    pytest.param(TokenEstimate, {"tokens": True, "source": "test"}, id="boolean-estimate"),
    pytest.param(TokenEstimate, {"tokens": 1, "source": ""}, id="empty-estimate-source"),
    pytest.param(HistoryEdit, {"message_count": -1}, id="negative-history-length"),
    pytest.param(BatchUpdate, {"message_count": True, "steps": ()}, id="boolean-batch-length"),
    pytest.param(ReplacePrefix, {"stop": 1, "summary": ""}, id="empty-summary"),
    pytest.param(ReplacePrefix, {"stop": True, "summary": "summary"}, id="boolean-stop"),
    pytest.param(ReplaceToolResult, {"message_index": -1, "tool_use_id": "a"}, id="negative-tool-index"),
    pytest.param(TokenUsage, {"input_tokens": True}, id="boolean-usage"),
])
def test_value_records_validate_at_construction(constructor, kwargs):
    with pytest.raises(ValueError):
        constructor(**kwargs)


def test_edit_and_batch_variants_cannot_mix_fields():
    with pytest.raises(TypeError):
        HistoryEdit(3, steps=())
    with pytest.raises(TypeError):
        BatchUpdate(3, (), prefix=ReplacePrefix(1, "summary"))
    children = [HistoryEdit(3)]
    batch = BatchUpdate(3, children)
    children.clear()
    assert len(batch.steps) == 1


def test_frozen_configuration_owns_nested_values():
    windows = {"main": 1000}
    kwargs = {"extra_headers": {"x-project": "original"}}
    options = CompactionOptions(context_windows=windows)
    strategy = Summarize(model_kwargs=kwargs)
    windows["main"] = -1
    kwargs["extra_headers"]["x-project"] = "caller edit"
    with pytest.raises(TypeError):
        options.context_windows["main"] = -1
    strategy.model_kwargs["extra_headers"]["x-project"] = "nested edit"
    assert options.context_windows["main"] == 1000
    assert strategy.model_kwargs["extra_headers"] == {"x-project": "original"}
    assert deepcopy(strategy).model_kwargs == strategy.model_kwargs


def test_normalized_usage_preserves_details_and_nested_stages():
    raw = {"input_tokens": 10, "provider": {"cache": "hit"}}
    usage = TokenUsage.from_dict(raw)
    raw["provider"]["cache"] = "external mutation"
    usage.details["provider"]["cache"] = "nested mutation"
    assert usage.input_tokens == 10
    assert usage.details == {"provider": {"cache": "hit"}}
    result = TokenUsage.combine([usage, TokenUsage.combine([TokenUsage(output_tokens=2)])])
    assert result.input_tokens == 10
    assert result.output_tokens == 2
    assert result.total_tokens is None  # Do not invent provider totals.
    assert result.stages[0] == usage
    assert result.stages[1].stages == (TokenUsage(output_tokens=2),)


async def test_trigger_has_no_services_credentials_or_strategy_state():
    class Inspect:
        def should_compact(self, context):
            assert set(vars(context)) == {"messages", "model", "tokens", "input_budget", "reason"}
            assert context.messages[0].content == "goal"
            context.messages[0].content = "trigger edit"
            return False

    runtime = CompactionRuntime(policy(trigger=Inspect()))
    runtime.state = {"plugin": "secret"}
    history = ConversationHistory([UserMessage("goal")])
    assert await run(runtime, history, model_kwargs={"api_key": "credential"}) == []
    assert history.messages[0].content == "goal"
    assert runtime.state == {"plugin": "secret"}


async def test_threshold_check_does_not_count_individual_messages():
    requests = []

    def counter(model, request):
        requests.append(request)
        return count_chars(model, request)

    runtime = CompactionRuntime(policy(token_counter=counter, trigger=TokenThreshold(tokens=99999)))
    history = ConversationHistory(old_history())
    assert await run(runtime, history) == []
    assert len(requests) == 1
    assert len(requests[0].messages) == len(history.messages)


async def test_cascade_reuses_counts_and_context_copies_stay_isolated():
    requests = []

    def counter(model, request):
        assert request not in requests, "Identical content was counted twice in one attempt"
        requests.append(deepcopy(request))
        return count_chars(model, request)

    class Inspect:
        async def compact(self, context: CompactionContext):
            assert context.message_tokens is context.message_tokens
            assert context.local_tokens is context.local_tokens
            child = context.with_state({"child": []})
            child.messages[0].content = "child edit"
            child.state["child"].append("edit")
            assert context.messages[0].content == "initial goal"
            assert context.state is None
            update = HistoryEdit(len(context.messages), prefix=ReplacePrefix(2, "checkpoint"))
            first, second = context.preview(update), context.preview(update)
            assert first.local_tokens == second.local_tokens
            assert first.local_tokens.tokens < context.local_tokens.tokens
            return CompactionResult(update)

    history = ConversationHistory([*old_history(), UserMessage("current")])
    runtime = CompactionRuntime(policy(strategy=cascade([Inspect()]), target_tokens=1,
                                       token_counter=counter))
    await run(runtime, history, manual=True)
    assert history.messages[-1].content == "current"
