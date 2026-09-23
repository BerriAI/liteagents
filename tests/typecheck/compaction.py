"""Static SDK contract examples, checked by mypy in CI (not executed by pytest)."""

from typing_extensions import assert_type

from liteagents import (
    BatchUpdate,
    CompactionCompleted,
    CompactionContext,
    CompactionOptions,
    CompactionResult,
    CompactionStrategy,
    CompactionTrigger,
    CompactionUpdate,
    HistoryEdit,
    ReplacePrefix,
    Summarize,
    TokenEstimate,
    TokenThreshold,
    TokenUsage,
    TriggerContext,
    any_of,
    cascade,
)


class CustomStrategy:
    async def compact(self, context: CompactionContext) -> CompactionResult | None:
        if len(context.messages) < 2:
            return None
        update = HistoryEdit(len(context.messages), prefix=ReplacePrefix(1, "summary"))
        assert_type(context.preview(update), CompactionContext)
        assert_type(context.local_tokens, TokenEstimate)
        return CompactionResult(update, usage=TokenUsage(input_tokens=10))


class CustomTrigger:
    def should_compact(self, context: TriggerContext) -> bool:
        return context.tokens.tokens > 100


def configure() -> CompactionOptions:
    strategy: CompactionStrategy = cascade([CustomStrategy(), cascade([Summarize()])])
    trigger: CompactionTrigger = any_of([CustomTrigger(), TokenThreshold(tokens=100)])
    return CompactionOptions(strategy=strategy, trigger=trigger, target_tokens=50)


def consume(event: CompactionCompleted) -> None:
    assert_type(event.before, TokenEstimate)
    assert_type(event.after, TokenEstimate)
    assert_type(event.usage, TokenUsage | None)
    assert_type(event.update, CompactionUpdate)
    if isinstance(event.update, BatchUpdate):
        assert_type(event.update.steps, tuple[CompactionUpdate, ...])
    else:
        assert_type(event.update, HistoryEdit)


def invalid_contracts(context: TriggerContext) -> None:
    # Unused-ignore checking verifies that these shapes remain static errors.
    HistoryEdit(1, steps=())  # type: ignore[call-arg]
    BatchUpdate(1, (), prefix=ReplacePrefix(1, "summary"))  # type: ignore[call-arg]
    _ = context.state  # type: ignore[attr-defined]
