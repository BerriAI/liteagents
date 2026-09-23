"""Select recent context to retain without splitting tool-call groups."""

from __future__ import annotations

from dataclasses import dataclass

from .base import CompactionContext


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
