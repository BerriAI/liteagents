"""Portable, pluggable context reduction. Strategies propose edits; the SDK commits them."""

from .base import (
    CompactionContext,
    CompactionError,
    CompactionResult,
    CompactionStrategy,
    CompactionTrigger,
    ContextBudgetExceeded,
)
from .options import CompactionOptions
from .retention import RecentTokens
from .strategies import PruneToolResults, Summarize
from .tokens import TokenCounter, TokenEstimate, context_window, estimate_tokens
from .triggers import TokenThreshold

__all__ = [
    "CompactionContext",
    "CompactionError",
    "CompactionOptions",
    "CompactionResult",
    "CompactionStrategy",
    "CompactionTrigger",
    "ContextBudgetExceeded",
    "PruneToolResults",
    "RecentTokens",
    "Summarize",
    "TokenCounter",
    "TokenEstimate",
    "TokenThreshold",
    "context_window",
    "estimate_tokens",
]
