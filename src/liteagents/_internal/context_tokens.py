"""Request-scoped usage anchors; never infer validity from retained message usage."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from ..compaction.tokens import TokenCounter, TokenCountRequest, TokenEstimate


def input_tokens(usage: dict[str, Any] | None) -> int | None:
    """The Messages API reports uncached input separately from cache reads/writes.

    Do not add output/total tokens: billed reasoning need not be replayed. Missing
    or malformed input usage cannot establish an anchor. Cache breakdowns inside
    cache_creation must not be counted again.
    """
    if not usage or "input_tokens" not in usage:
        return None
    counts = [usage["input_tokens"], *[
        0 if usage.get(key) is None else usage[key]
        for key in ("cache_read_input_tokens", "cache_creation_input_tokens")
    ]]
    if any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in counts):
        return None
    total = sum(counts)
    return total if total > 0 else None


@dataclass
class ContextTokens:
    model: str | None = None
    request: TokenCountRequest | None = field(default=None, repr=False)
    settings: dict[str, Any] = field(default_factory=dict, repr=False)
    tokens: int | None = None

    def observe(
        self, model: str, request: TokenCountRequest, settings: dict[str, Any],
        usage: dict[str, Any] | None, stop_reason: str | None,
    ) -> None:
        self.clear()
        count = input_tokens(usage)
        if count is None or stop_reason not in {"end_turn", "tool_use", "max_tokens", "stop_sequence"}:
            return
        self.model, self.request = model, deepcopy(request)
        self.settings, self.tokens = deepcopy(settings), count

    def clear(self) -> None:
        self.model, self.request, self.tokens = None, None, None
        self.settings = {}

    def count(
        self, model: str, request: TokenCountRequest, settings: dict[str, Any], counter: TokenCounter,
    ) -> TokenEstimate:
        local = counter(model, deepcopy(request))
        anchor = self.request
        if (anchor is None or self.tokens is None or model != self.model or settings != self.settings
            or request.system != anchor.system or request.tools != anchor.tools
            or request.messages[:len(anchor.messages)] != anchor.messages):
            # Once the context diverges, switching back must not resurrect stale usage.
            self.clear()
            return local
        # Difference of structured requests counts only appended content and avoids
        # charging the system, tools, and request framing twice. This includes the
        # replayed assistant response, regardless of its billed output/reasoning.
        previous = counter(model, deepcopy(anchor))
        delta = max(0, local.tokens - previous.tokens)
        return TokenEstimate(self.tokens + delta, f"response_usage+{local.source}")
