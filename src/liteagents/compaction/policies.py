"""Built-in token thresholds and recent-context retention policy."""

from __future__ import annotations

from dataclasses import dataclass

from .base import CompactionContext, CompactionError


@dataclass(frozen=True)
class TokenThreshold:
    """Exactly one of an absolute input-token estimate or a fraction of input budget."""

    tokens: int | None = None
    fraction: float | None = None

    def __post_init__(self) -> None:
        if (self.tokens is None) == (self.fraction is None):
            raise ValueError("Set exactly one of tokens or fraction")
        if self.tokens is not None and self.tokens < 1:
            raise ValueError("tokens must be positive")
        if self.fraction is not None and not 0 < self.fraction < 1:
            raise ValueError("fraction must be between 0 and 1 (exclusive)")

    def should_compact(self, context: CompactionContext) -> bool:
        if self.tokens is not None:
            return context.tokens.tokens >= self.tokens
        if context.input_budget is None:
            raise CompactionError(
                f"Unknown context window for {context.model!r}; set compaction.context_windows "
                "or use an absolute TokenThreshold(tokens=...)"
            )
        assert self.fraction is not None
        return context.tokens.tokens >= self.fraction * context.input_budget


@dataclass(frozen=True)
class RecentTokens:
    tokens: int = 12_000

    def __post_init__(self) -> None:
        if self.tokens < 1:
            raise ValueError("RecentTokens must be positive")

    def boundary(self, context: CompactionContext) -> int:
        """Keep at least this token estimate and the entire most recent tool group."""
        total, index = 0, len(context.messages)
        while index > 0 and total < self.tokens:
            index -= 1
            total += context.message_tokens[index]
        # Even a custom counter returning zero cannot discard the whole conversation.
        index = min(index, max(0, len(context.messages) - 1))
        return max(boundary for boundary in context.boundaries if boundary <= index)
