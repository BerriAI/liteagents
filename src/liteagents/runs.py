from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal

from .types import AssistantMessage, Message, TextBlock

RunStatus = Literal["running", "completed", "failed", "cancelled"]


@dataclass
class RunResult:
    run_id: str
    harness: str
    messages: list[Message]
    session_id: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Keep native usage units/fields; different harnesses report per-call or
        # per-turn totals, so summing them would manufacture misleading metrics.
        reported = [m.usage for m in self.messages if isinstance(m, AssistantMessage) and m.usage]
        if not self.usage and reported:
            self.usage = {"native_reports": reported}

    @property
    def text(self) -> str:
        for message in reversed(self.messages):
            if isinstance(message, AssistantMessage):
                text = "".join(
                    block.text for block in message.content if isinstance(block, TextBlock)
                )
                if text and message.stop_reason != "tool_use":
                    return text
        return ""


class LocalRun:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.status: RunStatus = "running"
        self._result: RunResult | None = None
        self._error: BaseException | None = None
        self._done = asyncio.Event()

    def finish(self, result: RunResult) -> None:
        self._result = result
        self.status = "completed"
        self._done.set()

    def fail(self, error: BaseException) -> None:
        self._error = error
        self.status = (
            "cancelled" if isinstance(error, (asyncio.CancelledError, GeneratorExit)) else "failed"
        )
        self._done.set()

    async def result(self) -> RunResult:
        await self._done.wait()
        if self._error:
            if self.status == "cancelled":
                raise asyncio.CancelledError("Agent run was cancelled")
            raise self._error
        assert self._result is not None
        return self._result
