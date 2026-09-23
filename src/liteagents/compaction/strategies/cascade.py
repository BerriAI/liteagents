"""Compose ordinary strategies using detached previews and one atomic batch."""

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any

from ...types import CompactionUpdate
from ..base import CompactionContext, CompactionError, CompactionResult, CompactionStrategy


def _usage(stages: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not stages:
        return None
    # Preserve every provider's full usage while totaling common token fields.
    totals: dict[str, Any] = {"stages": deepcopy(stages)}
    for key in ("input_tokens", "output_tokens", "total_tokens",
                "cache_creation_input_tokens", "cache_read_input_tokens"):
        values = [stage[key] for stage in stages if key in stage]
        if values and all(isinstance(value, (int, float)) and not isinstance(value, bool)
                          for value in values):
            totals[key] = sum(values)
    return totals


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
        usages: list[dict[str, Any]] = []
        try:
            for index, strategy in enumerate(self.strategies):
                if candidate.tokens.tokens <= context.target_tokens:
                    break
                child = replace(deepcopy(candidate), state=deepcopy(states[index]))
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
                local_before = candidate.preview(CompactionUpdate(len(candidate.messages)))
                if preview.tokens.tokens >= local_before.tokens.tokens:
                    continue
                candidate = preview
                updates.append(deepcopy(result.update))
                states[index] = deepcopy(result.state)
        except Exception as exc:
            if isinstance(exc, CompactionError):
                exc.usage = _usage(usages)
                raise
            raise CompactionError(str(exc), usage=_usage(usages)) from exc
        if not updates and not usages:
            return None
        return CompactionResult(
            CompactionUpdate(len(context.messages), steps=tuple(updates)),
            state=tuple(states), usage=_usage(usages),
        )


def cascade(strategies: Sequence[CompactionStrategy]) -> Cascade:
    """Try strategies in order until target_tokens is met; propagate errors.

    Each successful reduction feeds the next strategy. Non-shrinking proposals
    are skipped. Child state is isolated by position and committed with the
    final batch. Exhausting stages may miss the soft target; the runtime still
    enforces the hard model input budget before committing any changes.
    """
    return Cascade(tuple(strategies))
