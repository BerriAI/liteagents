"""Boolean composition and retained-turn trigger scenarios."""

from dataclasses import replace

import pytest

from liteagents import (
    CompactionContext,
    CompactionError,
    SummaryMessage,
    TextBlock,
    TokenEstimate,
    TurnThreshold,
    UserMessage,
    all_of,
    any_of,
)

from .compaction_helpers import pair


@pytest.fixture
def context():
    return CompactionContext(
        messages=(UserMessage("goal"),), model="main", tokens=TokenEstimate(100, "test"),
        input_budget=1000, message_tokens=(100,), boundaries=(0, 1), reason="threshold",
    )


@pytest.mark.parametrize("factory,values,expected,visited", [
    pytest.param(any_of, [False, False], False, [0, 1], id="any-no-match"),
    pytest.param(any_of, [False, True], True, [0, 1], id="any-later-match"),
    pytest.param(any_of, [True, False], True, [0], id="any-first-match"),
    pytest.param(any_of, [True, True], True, [0], id="any-short-circuits"),
    pytest.param(all_of, [False, False], False, [0], id="all-first-miss"),
    pytest.param(all_of, [False, True], False, [0], id="all-short-circuits"),
    pytest.param(all_of, [True, False], False, [0, 1], id="all-later-miss"),
    pytest.param(all_of, [True, True], True, [0, 1], id="all-match"),
])
def test_composite_trigger_evaluates_in_order(context, factory, values, expected, visited):
    calls = []

    class Trigger:
        def __init__(self, index):
            self.index = index

        def should_compact(self, context):
            calls.append(self.index)
            return values[self.index]

    children = [Trigger(i) for i in range(len(values))]
    trigger = factory(children)
    assert calls == []  # Construction is declarative, not immediate evaluation.
    children.clear()  # The configuration owns its sequence.
    assert trigger.should_compact(context) is expected
    assert calls == visited


@pytest.mark.parametrize("factory,first_value", [
    pytest.param(any_of, False, id="any"),
    pytest.param(all_of, True, id="all"),
])
def test_children_cannot_mutate_each_others_context(context, factory, first_value):
    context = replace(context, state={"value": "original"})

    class Mutator:
        def should_compact(self, context):
            context.messages[0].content = "changed"
            context.state["value"] = "changed"
            return first_value

    class Observer:
        def should_compact(self, context):
            assert context.messages[0].content == "goal"
            assert context.state == {"value": "original"}
            return True

    assert factory([Mutator(), Observer()]).should_compact(context)
    assert context.messages[0].content == "goal"
    assert context.state == {"value": "original"}


@pytest.mark.parametrize("factory", [any_of, all_of], ids=["any", "all"])
def test_composites_reject_empty_configuration_and_propagate_errors(context, factory):
    with pytest.raises(ValueError, match="at least one"):
        factory([])

    class Broken:
        def should_compact(self, context):
            raise CompactionError("cannot evaluate")

    with pytest.raises(CompactionError, match="cannot evaluate"):
        factory([Broken()]).should_compact(context)


@pytest.mark.parametrize("turns,expected", [
    pytest.param(1, True, id="above-threshold"),
    pytest.param(2, True, id="at-threshold"),
    pytest.param(3, False, id="below-threshold"),
])
def test_turn_threshold_ignores_tool_results_and_generated_summaries(context, turns, expected):
    messages = (SummaryMessage("earlier work"), UserMessage("first"), *pair("a", "result"),
                UserMessage([TextBlock("second")]), *pair("b", "result"))
    assert TurnThreshold(turns).should_compact(replace(context, messages=messages)) is expected


def test_nested_composites_use_the_same_trigger_protocol(context):
    trigger = all_of([TurnThreshold(1), any_of([TurnThreshold(3), TurnThreshold(1)])])
    assert trigger.should_compact(context)


@pytest.mark.parametrize("turns", [0, -1])
def test_turn_threshold_requires_positive_count(turns):
    with pytest.raises(ValueError, match="positive"):
        TurnThreshold(turns)
