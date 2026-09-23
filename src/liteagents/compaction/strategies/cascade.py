"""Compose ordinary strategies using detached previews and one atomic batch."""

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass

from ...types import BatchUpdate, CompactionUpdate
from ...usage import TokenUsage
from ..base import CompactionContext, CompactionError, CompactionResult, CompactionStrategy


@dataclass(frozen=True)
class Cascade:
    strategies: tuple[CompactionStrategy, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "strategies", tuple(self.strategies))
        if not self.strategies:
            raise ValueError("cascade requires at least one strategy")

    async def compact(self, context: CompactionContext) -> CompactionResult | None:
        if context.target_tokens is None:
            raise CompactionError("cascade requires CompactionOptions.target_tokens")
        states = ([None] * len(self.strategies) if context.state is None
                  else deepcopy(context.state))
        if not isinstance(states, (tuple, list)) or len(states) != len(self.strategies):
            raise CompactionError("Cascade state must contain one entry per strategy")
        states = list(states)
        candidate = context
        updates: list[CompactionUpdate] = []
        usages: list[TokenUsage] = []
        try:
            for index, strategy in enumerate(self.strategies):
                if candidate.tokens.tokens <= context.target_tokens:
                    break
                child = candidate.with_state(states[index])
                try:
                    result = await strategy.compact(child)
                except CompactionError as exc:
                    if exc.usage is not None:
                        usages.append(deepcopy(exc.usage))
                    raise
                if result is None:
                    continue
                if result.usage is not None:
                    usages.append(deepcopy(result.usage))
                preview = candidate.preview(result.update)
                # Compare both histories with the same local counter. Initial
                # context.tokens may use a differently calibrated usage anchor.
                if preview.local_tokens.tokens >= candidate.local_tokens.tokens:
                    continue
                candidate = preview
                updates.append(deepcopy(result.update))
                states[index] = deepcopy(result.state)
        except Exception as exc:
            if isinstance(exc, CompactionError):
                exc.usage = TokenUsage.combine(usages)
                raise
            raise CompactionError(str(exc), usage=TokenUsage.combine(usages)) from exc
        if not updates and not usages:
            return None
        return CompactionResult(
            BatchUpdate(len(context.messages), steps=tuple(updates)),
            state=tuple(states), usage=TokenUsage.combine(usages),
        )


def cascade(strategies: Sequence[CompactionStrategy]) -> Cascade:
    """Try strategies in order until target_tokens is met; propagate errors.

    Each successful reduction feeds the next strategy. Non-shrinking proposals
    are skipped. Child state is isolated by position and committed with the
    final batch. Exhausting stages may miss the soft target; the runtime still
    enforces the hard model input budget before committing any changes.
    """
    return Cascade(tuple(strategies))
