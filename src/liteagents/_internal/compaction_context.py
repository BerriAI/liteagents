"""Runtime-owned snapshots, lazy measurements, and strategy capabilities."""

import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
from functools import cached_property
from typing import Any

import litellm

from ..compaction.base import CompactionError, ContextBudgetExceeded
from ..compaction.options import CompactionOptions
from ..compaction.tokens import TokenCountRequest, context_window
from ..history import ConversationHistory, safe_boundaries
from ..types import CompactionReason, CompactionUpdate, Message, TokenEstimate, WireTool
from ..usage import TokenUsage
from .adapter import extract_response_fields
from .validation import OwnedMapping


class TokenMeasurements:
    """One compaction attempt's cache; never shared across changing configuration."""

    def __init__(self, options: CompactionOptions) -> None:
        self._counter = options.token_counter
        self._counts: dict[tuple[str, str], TokenEstimate] = {}

    def count(self, model: str, request: TokenCountRequest) -> TokenEstimate:
        key = (model, json.dumps((request.messages, request.system, request.tools),
                                ensure_ascii=False))
        if key not in self._counts:
            self._counts[key] = self._counter(model, deepcopy(request))
        return self._counts[key]


@dataclass(frozen=True)
class HistorySnapshot:
    history: ConversationHistory = field(repr=False)
    model: str
    system: str | None
    tools: tuple[WireTool, ...]
    measurements: TokenMeasurements = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "tools", deepcopy(tuple(self.tools)))

    @cached_property
    def request(self) -> TokenCountRequest:
        return TokenCountRequest(tuple(self.history.raw()), self.system, self.tools)

    @cached_property
    def local_tokens(self) -> TokenEstimate:
        return self.measurements.count(self.model, self.request)

    @cached_property
    def message_tokens(self) -> tuple[int, ...]:
        return tuple(self.measurements.count(self.model, TokenCountRequest((message,))).tokens
                     for message in self.request.messages)

    @cached_property
    def boundaries(self) -> tuple[int, ...]:
        return safe_boundaries(self.history.messages)

    def apply(self, update: CompactionUpdate) -> "HistorySnapshot":
        return replace(self, history=self.history.compacted(update))


@dataclass(frozen=True)
class SummaryService:
    options: CompactionOptions
    measurements: TokenMeasurements
    model_kwargs: Mapping[str, Any] = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_kwargs", OwnedMapping(self.model_kwargs))

    async def generate(
        self, text: str, *, system: str, model: str, max_tokens: int,
        model_kwargs: Mapping[str, Any],
    ) -> tuple[str, TokenUsage | None]:
        window = context_window(model, self.options.context_windows)
        if window is None:
            raise CompactionError(
                f"Unknown summary model context window for {model!r}; set compaction.context_windows"
            )
        request = TokenCountRequest(({"role": "user", "content": text},), system)
        estimate = self.measurements.count(model, request)
        if estimate.tokens + max_tokens + self.options.safety_margin > window:
            raise ContextBudgetExceeded(
                f"Summary input does not fit {model!r}; choose a larger summary model or compact earlier"
            )
        generation_settings = {
            "thinking", "reasoning", "reasoning_effort", "output_config", "response_format",
            "stop_sequences", "stop", "context_management", "compaction",
        }
        kwargs = {key: value for key, value in self.model_kwargs.items() if key not in generation_settings}
        kwargs.update(model_kwargs)
        response = await litellm.anthropic_messages(
            model=model, messages=list(request.messages), system=system, max_tokens=max_tokens,
            tools=None, tool_choice=None, stream=False, **kwargs,
        )
        content, stop_reason, _, usage = extract_response_fields(response)
        summary = "\n".join(block.get("text", "") for block in content if block.get("type") == "text")
        if stop_reason != "end_turn" or not summary.strip():
            raise CompactionError("Summary response was incomplete or contained no summary text", usage=usage)
        return summary, usage


@dataclass(frozen=True)
class RuntimeContext:
    _snapshot: HistorySnapshot = field(repr=False)
    _summary: SummaryService = field(repr=False)
    tokens: TokenEstimate
    input_budget: int | None
    reason: CompactionReason
    target_tokens: int | None = None
    instructions: str | None = None
    state: Any = field(default=None, repr=False)

    @cached_property
    def messages(self) -> tuple[Message, ...]:
        return tuple(deepcopy(self._snapshot.history.messages))

    @property
    def model(self) -> str:
        return self._snapshot.model

    @property
    def system(self) -> str | None:
        return self._snapshot.system

    @property
    def local_tokens(self) -> TokenEstimate:
        return self._snapshot.local_tokens

    @property
    def message_tokens(self) -> tuple[int, ...]:
        return self._snapshot.message_tokens

    @property
    def boundaries(self) -> tuple[int, ...]:
        return self._snapshot.boundaries

    def preview(self, update: CompactionUpdate) -> "RuntimeContext":
        snapshot = self._snapshot.apply(update)
        return replace(self, _snapshot=snapshot, tokens=snapshot.local_tokens, state=deepcopy(self.state))

    def with_state(self, state: Any) -> "RuntimeContext":
        return replace(self, state=deepcopy(state))

    def count_tokens(self, request: TokenCountRequest, *, model: str | None = None) -> TokenEstimate:
        return self._snapshot.measurements.count(model or self.model, request)

    async def generate_summary(
        self, text: str, *, system: str, model: str, max_tokens: int,
        model_kwargs: Mapping[str, Any],
    ) -> tuple[str, TokenUsage | None]:
        return await self._summary.generate(text, system=system, model=model,
                                            max_tokens=max_tokens, model_kwargs=model_kwargs)
