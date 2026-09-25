"""Trigger compaction at an absolute token count or fraction of the input budget."""

from __future__ import annotations

from dataclasses import dataclass

from ..._internal.validation import fraction, integer
from ..base import CompactionError, TriggerContext


@dataclass(frozen=True)
class TokenThreshold:
    """Exactly one of an absolute input-token estimate or a fraction of input budget."""

    tokens: int | None = None
    fraction: float | None = None

    def __post_init__(self) -> None:
        if (self.tokens is None) == (self.fraction is None):
            raise ValueError("Set exactly one of tokens or fraction")
        if self.tokens is not None:
            integer(self.tokens, "tokens", minimum=1)
        if self.fraction is not None:
            fraction(self.fraction, "fraction")

    def should_compact(self, context: TriggerContext) -> bool:
        if self.tokens is not None:
            return context.tokens.tokens >= self.tokens
        if context.input_budget is None:
            raise CompactionError(
                f"Unknown context window for {context.model!r}; set compaction.context_windows "
                "or use an absolute TokenThreshold(tokens=...)"
            )
        assert self.fraction is not None
        return context.tokens.tokens >= self.fraction * context.input_budget
