"""Compaction configuration and default policies."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .base import CompactionStrategy, CompactionTrigger
from .policies import TokenThreshold
from .summarize import Summarize
from .tokens import TokenCounter, estimate_tokens


@dataclass(frozen=True)
class CompactionOptions:
    strategy: CompactionStrategy = field(default_factory=Summarize)
    trigger: CompactionTrigger | None = field(default_factory=lambda: TokenThreshold(fraction=0.8))
    context_windows: Mapping[str, int] = field(default_factory=dict)
    token_counter: TokenCounter = estimate_tokens
    safety_margin: int = 1024

    def __post_init__(self) -> None:
        if self.safety_margin < 0 or any(window < 1 for window in self.context_windows.values()):
            raise ValueError("Context windows must be positive and safety_margin nonnegative")
