"""Strategy contracts and the limited data view exposed to triggers."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..types import CompactionReason, CompactionUpdate, Message, TokenEstimate
from ..usage import TokenUsage
from .tokens import TokenCountRequest


class CompactionError(RuntimeError):
    """Compaction failed; history and strategy state are unchanged."""

    def __init__(self, message: str, *, usage: TokenUsage | None = None) -> None:
        super().__init__(message)
        self.usage = usage


class ContextBudgetExceeded(CompactionError):
    """The estimated request exceeds the selected model's input budget."""


@dataclass(frozen=True)
class TriggerContext:
    """Detached history and budget data; no credentials, services, or plugin state."""

    messages: tuple[Message, ...]
    model: str
    tokens: TokenEstimate
    input_budget: int | None
    reason: CompactionReason


class CompactionContext(Protocol):
    """Runtime-owned strategy context. Previewing and counting never commit edits.

    Per-conversation plugin state is opaque and detached; return it in the result
    to commit it. with_state() creates an isolated child context for composition.
    """

    @property
    def messages(self) -> tuple[Message, ...]: ...
    @property
    def model(self) -> str: ...
    @property
    def tokens(self) -> TokenEstimate: ...
    @property
    def local_tokens(self) -> TokenEstimate: ...
    @property
    def input_budget(self) -> int | None: ...
    @property
    def message_tokens(self) -> tuple[int, ...]: ...
    @property
    def boundaries(self) -> tuple[int, ...]: ...
    @property
    def reason(self) -> CompactionReason: ...
    @property
    def system(self) -> str | None: ...
    @property
    def instructions(self) -> str | None: ...
    @property
    def state(self) -> Any: ...
    @property
    def target_tokens(self) -> int | None: ...

    def preview(self, update: CompactionUpdate) -> "CompactionContext": ...
    def with_state(self, state: Any) -> "CompactionContext": ...
    def count_tokens(self, request: TokenCountRequest, *, model: str | None = None) -> TokenEstimate: ...
    async def generate_summary(
        self, text: str, *, system: str, model: str, max_tokens: int,
        model_kwargs: Mapping[str, Any],
    ) -> tuple[str, TokenUsage | None]: ...


@dataclass(frozen=True)
class CompactionResult:
    update: CompactionUpdate
    state: Any = field(default=None, repr=False)
    usage: TokenUsage | None = None


class CompactionStrategy(Protocol):
    async def compact(self, context: CompactionContext) -> CompactionResult | None: ...


class CompactionTrigger(Protocol):
    def should_compact(self, context: TriggerContext) -> bool: ...
