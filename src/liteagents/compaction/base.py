"""Strategy and trigger contracts, detached context, and compaction errors."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
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
    target_tokens: int | None = None
    _preview: Callable[[CompactionUpdate], CompactionContext] | None = field(
        default=None, repr=False, compare=False,
    )

    def preview(self, update: CompactionUpdate) -> CompactionContext:
        """Validate edits and recount a detached candidate without committing.

        Available on runtime-provided contexts. Retains raw provider blocks and
        includes system/tool overhead when counting. The returned context can
        preview further edits; indices refer to its own candidate history.
        """
        if self._preview is None:
            raise CompactionError("This context does not support previewing edits")
        return replace(self._preview(update), state=deepcopy(self.state))


@dataclass(frozen=True)
class CompactionResult:
    update: CompactionUpdate
    state: Any = field(default=None, repr=False)
    usage: dict[str, Any] | None = None


class CompactionStrategy(Protocol):
    async def compact(self, context: CompactionContext) -> CompactionResult | None: ...


class CompactionTrigger(Protocol):
    def should_compact(self, context: CompactionContext) -> bool: ...
