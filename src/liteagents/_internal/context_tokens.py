"""Request-scoped usage anchors; never trust usage attached to imported history."""

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from ..compaction.tokens import TokenCounter, TokenCountRequest
from ..types import TokenEstimate
from ..usage import TokenUsage


@dataclass(frozen=True)
class UsageAnchor:
    model: str
    request: TokenCountRequest = field(repr=False)
    settings: dict[str, Any] = field(repr=False)
    tokens: int


class ContextTokens:
    def __init__(self) -> None:
        self._anchor: UsageAnchor | None = None

    def observe(
        self, model: str, request: TokenCountRequest, settings: dict[str, Any],
        usage: TokenUsage | None, stop_reason: str | None,
    ) -> None:
        self.clear()
        count = usage.context_input_tokens if usage is not None else None
        if count is not None and stop_reason in {"end_turn", "tool_use", "max_tokens", "stop_sequence"}:
            self._anchor = UsageAnchor(model, deepcopy(request), deepcopy(settings), count)

    def clear(self) -> None:
        self._anchor = None

    def count(
        self, model: str, request: TokenCountRequest, settings: dict[str, Any], counter: TokenCounter,
    ) -> TokenEstimate:
        local = counter(model, deepcopy(request))
        anchor = self._anchor
        if (anchor is None or model != anchor.model or settings != anchor.settings
            or request.system != anchor.request.system or request.tools != anchor.request.tools
            or request.messages[:len(anchor.request.messages)] != anchor.request.messages):
            self.clear()
            return local
        previous = counter(model, deepcopy(anchor.request))
        delta = max(0, local.tokens - previous.tokens)
        return TokenEstimate(anchor.tokens + delta, f"response_usage+{local.source}")
