"""Strategy and trigger contracts, detached context, and compaction errors."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..types import CompactionReason, CompactionUpdate, Message
from .tokens import TokenCounter, TokenEstimate, estimate_tokens


class CompactionError(RuntimeError):
    """Compaction could not safely produce a smaller context. History is unchanged."""

    def __init__(self, message: str, *, usage: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.usage = usage


class ContextBudgetExceeded(CompactionError):
    """The estimated request still exceeds the selected model's input budget."""


@dataclass(frozen=True)
class CompactionContext:
    """Detached snapshot. Mutating nested values cannot change the client's history.

    Strategies must keep conversation-specific bookkeeping in `state`, not on the
    shared strategy instance. Boundaries/indices address this snapshot only.
    """

    messages: tuple[Message, ...]
    model: str
    tokens: TokenEstimate
    input_budget: int | None
    message_tokens: tuple[int, ...]
    boundaries: tuple[int, ...]
    reason: CompactionReason
    system: str | None = None
    instructions: str | None = None
    state: Any = field(default=None, repr=False)
    model_kwargs: Mapping[str, Any] = field(default_factory=dict, repr=False)
    context_windows: Mapping[str, int] = field(default_factory=dict)
    token_counter: TokenCounter = estimate_tokens
    safety_margin: int = 1024


@dataclass(frozen=True)
class CompactionResult:
    update: CompactionUpdate
    state: Any = field(default=None, repr=False)
    usage: dict[str, Any] | None = None


class CompactionStrategy(Protocol):
    async def compact(self, context: CompactionContext) -> CompactionResult | None: ...


class CompactionTrigger(Protocol):
    def should_compact(self, context: CompactionContext) -> bool: ...
