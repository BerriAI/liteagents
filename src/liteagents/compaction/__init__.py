"""Portable, pluggable context reduction. Strategies propose edits; the SDK commits them."""

from ..types import TokenEstimate
from .base import (
    CompactionContext,
    CompactionError,
    CompactionResult,
    CompactionStrategy,
    CompactionTrigger,
    ContextBudgetExceeded,
    TriggerContext,
)
from .options import CompactionOptions
from .retention import RecentTokens
from .strategies import Cascade, PruneToolResults, Summarize, cascade
from .tokens import (
    TokenCounter,
    TokenCountRequest,
    context_window,
    estimate_tokens,
    heuristic_tokens,
)
from .triggers import AllOf, AnyOf, TokenThreshold, TurnThreshold, all_of, any_of

__all__ = [
    "AllOf",
    "AnyOf",
    "Cascade",
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
    "TokenCountRequest",
    "TokenCounter",
    "TokenEstimate",
    "TokenThreshold",
    "TriggerContext",
    "TurnThreshold",
    "all_of",
    "any_of",
    "cascade",
    "context_window",
    "estimate_tokens",
    "heuristic_tokens",
]
