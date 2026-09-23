"""Compaction configuration and default policies."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .._internal.validation import OwnedMapping, integer, nonempty
from .base import CompactionStrategy, CompactionTrigger
from .strategies import Summarize
from .tokens import TokenCounter, estimate_tokens
from .triggers import TokenThreshold


@dataclass(frozen=True)
class CompactionOptions:
    strategy: CompactionStrategy = field(default_factory=Summarize)
    trigger: CompactionTrigger | None = field(default_factory=lambda: TokenThreshold(fraction=0.8))
    context_windows: Mapping[str, int] = field(default_factory=dict)
    token_counter: TokenCounter = estimate_tokens
    safety_margin: int = 1024
    target_tokens: int | None = None

    def __post_init__(self) -> None:
        integer(self.safety_margin, "safety_margin")
        for model, window in self.context_windows.items():
            nonempty(model, "model")
            integer(window, "context window", minimum=1)
        if self.target_tokens is not None:
            integer(self.target_tokens, "target_tokens", minimum=1)
        object.__setattr__(self, "context_windows", OwnedMapping(self.context_windows))
