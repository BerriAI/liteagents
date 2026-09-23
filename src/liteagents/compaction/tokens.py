"""Structured token estimates and model context-window lookup."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol, cast

import litellm

from ..types import TokenEstimate, WireMessage, WireTool


@dataclass(frozen=True)
class TokenCountRequest:
    """Detached Anthropic-format content; contains no connection credentials.

    Used for whole requests, individual messages, and proposed compaction edits.
    Counters must be deterministic and must not mutate the supplied content.
    """

    messages: tuple[WireMessage, ...]
    system: str | None = None
    tools: tuple[WireTool, ...] = ()


class TokenCounter(Protocol):
    def __call__(self, model: str, request: TokenCountRequest) -> TokenEstimate: ...


def _opaque_bytes(content: Any) -> int:
    """Project known opaque blocks only in the detached counting copy.

    LiteLLM cannot tokenize redacted_thinking. Charge its serialized UTF-8 bytes
    conservatively instead; never decode it or alter provider replay content.
    Other unsupported blocks still raise through the normal counter.
    """
    surcharge = 0
    if isinstance(content, list):
        for index, block in enumerate(content):
            if isinstance(block, dict) and block.get("type") == "redacted_thinking":
                surcharge += len(json.dumps(block, ensure_ascii=False).encode("utf-8"))
                content[index] = {"type": "text", "text": ""}
            elif isinstance(block, dict) and block.get("type") == "tool_result":
                surcharge += _opaque_bytes(block.get("content"))
    return surcharge


def estimate_tokens(model: str, request: TokenCountRequest) -> TokenEstimate:
    """LiteLLM's local model-aware estimate, including system/tool overhead.

    Uses default image dimensions without fetching image URLs. LiteLLM may use
    a fallback tokenizer for unknown models; this is never a provider guarantee.
    Known opaque reasoning blocks reserve one token per serialized UTF-8 byte.
    Other errors propagate: choose heuristic_tokens explicitly if acceptable.
    """
    if litellm.disable_token_counter:
        raise ValueError("LiteLLM token counting is disabled; configure a compaction token_counter")
    messages = deepcopy(list(request.messages))
    opaque = sum(_opaque_bytes(message.get("content")) for message in messages)
    if request.system is not None:
        messages.insert(0, {"role": "system", "content": request.system})
    tokens = litellm.token_counter(
        model=model, messages=messages, tools=cast(Any, deepcopy(list(request.tools)) or None),
        use_default_image_token_count=True,
    )
    source = "litellm.token_counter+opaque_utf8_bytes" if opaque else "litellm.token_counter"
    return TokenEstimate(tokens + opaque, source)


def heuristic_tokens(model: str, request: TokenCountRequest) -> TokenEstimate:
    """Explicit UTF-8 bytes/3 fallback; unsuitable for accurate multimodal counts."""
    text = json.dumps({"messages": request.messages, "system": request.system,
                       "tools": request.tools or None}, ensure_ascii=False)
    return TokenEstimate(math.ceil(len(text.encode("utf-8")) / 3), "utf8_bytes/3")


def context_window(model: str, overrides: Mapping[str, int]) -> int | None:
    if model in overrides:
        return overrides[model]
    try:
        info = litellm.get_model_info(model)
        value = info.get("max_input_tokens") or info.get("max_tokens")
        return int(value) if value else None
    except Exception:  # noqa: BLE001 -- metadata lookup is optional for unknown gateway models
        return None
