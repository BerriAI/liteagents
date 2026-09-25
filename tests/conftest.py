"""Shared fixtures for the legacy LiteLLM loop regression tests.

The v2 tests separately exercise native harnesses, MCP, and Temporal.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import litellm
import pytest


class QueuedResponses:
    """Pops one canned response per call; raises if called more times than
    prepared, so an accidental infinite loop fails the test instead of
    hanging it.
    """

    def __init__(self) -> None:
        self._queue: list[dict[str, Any] | Callable[..., dict[str, Any]]] = []
        self.calls: list[dict[str, Any]] = []

    def push(self, response: dict[str, Any] | Callable[..., dict[str, Any]]) -> None:
        self._queue.append(response)

    async def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if not self._queue:
            raise AssertionError(
                f"anthropic_messages called more times than expected (call #{len(self.calls)})"
            )
        item = self._queue.pop(0)
        return item(**kwargs) if callable(item) else item


@pytest.fixture
def mock_anthropic_messages(monkeypatch: pytest.MonkeyPatch) -> QueuedResponses:
    queued = QueuedResponses()
    monkeypatch.setattr(litellm, "anthropic_messages", queued)
    return queued


def text_response(text: str, *, model: str, stop_reason: str = "end_turn") -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "model": model,
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def tool_use_response(*, tool_use_id: str, name: str, input: dict[str, Any], model: str) -> dict[str, Any]:
    return {
        "content": [{"type": "tool_use", "id": tool_use_id, "name": name, "input": input}],
        "model": model,
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
