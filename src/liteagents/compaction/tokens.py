"""Token estimates and model context-window lookup."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import litellm


@dataclass(frozen=True)
class TokenEstimate:
    tokens: int
    source: str

    def __post_init__(self) -> None:
        if self.tokens < 0 or not self.source:
            raise ValueError("Token estimates require nonnegative tokens and a source")


class TokenCounter(Protocol):
    def __call__(self, model: str, text: str) -> TokenEstimate:
        """Estimate a serialized request (or message); never receives credentials."""
        ...


def estimate_tokens(model: str, text: str) -> TokenEstimate:
    """Offline heuristic, including JSON overhead. Not a provider token-count guarantee.

    Counting UTF-8 bytes is conservative for common text, but multimodal/provider
    accounting differs. Supply a TokenCounter for tokenizer-specific estimates.
    """
    return TokenEstimate(math.ceil(len(text.encode("utf-8")) / 3), "utf8_bytes/3")


def context_window(model: str, overrides: Mapping[str, int]) -> int | None:
    if model in overrides:
        return overrides[model]
    try:
        info = litellm.get_model_info(model)
        value = info.get("max_input_tokens") or info.get("max_tokens")
        return int(value) if value else None
    except Exception:  # noqa: BLE001 -- metadata lookup is optional for unknown gateway models
        # Gateway aliases and user-defined models need caller-owned metadata.
        return None
