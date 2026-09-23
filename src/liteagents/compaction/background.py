"""Opt-in asynchronous working memory configuration."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .._internal.validation import OwnedMapping, integer, nonempty
from .tokens import TokenCounter, estimate_tokens


@dataclass(frozen=True)
class BackgroundMemoryOptions:
    """Prepare memory concurrently; wait rather than drop unprocessed history.

    Limits are token-counter estimates. Transcript storage lasts for this client
    only; applications continue to own persistence. The observer has no tools.
    """

    model: str
    max_recent_turns: int | None = None
    max_context_tokens: int = 24_000
    max_memory_tokens: int = 2_000
    max_observation_tokens: int = 12_000
    min_observation_tokens: int = 1024
    timeout: float = 60.0
    instructions: str | None = None
    model_kwargs: Mapping[str, Any] = field(default_factory=dict, repr=False)
    context_windows: Mapping[str, int] = field(default_factory=dict)
    token_counter: TokenCounter = estimate_tokens
    safety_margin: int = 1024

    def __post_init__(self) -> None:
        nonempty(self.model, "model")
        for name in ("max_context_tokens", "max_memory_tokens", "max_observation_tokens",
                     "min_observation_tokens"):
            integer(getattr(self, name), name, minimum=1)
        if self.max_recent_turns is not None:
            integer(self.max_recent_turns, "max_recent_turns", minimum=1)
        if (isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float))
                or not math.isfinite(self.timeout) or self.timeout <= 0):
            raise ValueError("Memory timeout must be finite and positive")
        integer(self.safety_margin, "safety_margin")
        for model, window in self.context_windows.items():
            nonempty(model, "model")
            integer(window, "context window", minimum=1)
        object.__setattr__(self, "context_windows", OwnedMapping(self.context_windows))
        object.__setattr__(self, "model_kwargs", OwnedMapping(self.model_kwargs))
        reserved = {"model", "messages", "system", "max_tokens", "tools", "tool_choice", "stream"}
        if reserved.intersection(self.model_kwargs):
            raise ValueError("Memory model_kwargs cannot replace request-owned fields")


@dataclass(frozen=True)
class MemorySnapshot:
    """Published notes and their exact coverage in the original transcript.

    Message IDs are one-based. `processed_through=0` means nothing is covered.
    The SDK, not the observer model, assigns version and coverage.
    """

    version: int = 0
    processed_through: int = 0
    notes: str = ""
