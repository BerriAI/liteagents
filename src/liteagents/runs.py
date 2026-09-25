from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal

from .types import AssistantMessage, Message, TextBlock

RunStatus = Literal["running", "waiting_for_approval", "completed", "failed", "cancelled"]


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
        self._status: RunStatus = "running"
        self._result: RunResult | None = None
        self._error: BaseException | None = None
        self._done = asyncio.Event()
        self._settled = asyncio.Event()
        self.control: Any = None
        self.task: asyncio.Task | None = None
        self._events: list[dict[str, Any]] = []
        self._changed = asyncio.Event()

    def finish(self, result: RunResult) -> None:
        self._result = result
        self._status = "completed"
        self._done.set()
        self._changed.set()

    def fail(self, error: BaseException) -> None:
        self._error = error
        self._status = (
            "cancelled" if isinstance(error, (asyncio.CancelledError, GeneratorExit)) else "failed"
        )
        self._done.set()
        self._changed.set()

    async def status(self) -> RunStatus:
        return self._status

    async def result(self) -> RunResult:
        await self._done.wait()
        if self._error:
            if self._status == "cancelled":
                raise asyncio.CancelledError("Agent run was cancelled")
            raise self._error
        assert self._result is not None
        return self._result

    async def emit(self, payload: dict[str, Any]) -> None:
        if payload["kind"] == "approval_requested":
            self._status = "waiting_for_approval"
        self._events.append({"cursor": len(self._events) + 1, **payload})
        self._changed.set()

    async def events(self, after: int = 0):
        from .runtime.events import envelope

        while True:
            self._changed.clear()
            for event in self._events:
                if event["cursor"] > after:
                    after = event["cursor"]
                    yield envelope(event)
            if self._done.is_set():
                return
            await self._changed.wait()

    async def approvals(self):
        return await self.control.pending() if self.control else []

    async def approve(self, approval_id: str, *, allow: bool = True):
        if self.control:
            await self.control.approve(approval_id, allow)
        if not self._done.is_set():
            self._status = "waiting_for_approval" if await self.approvals() else "running"

    async def cancel(self):
        if self.control:
            self.control.cancelled = True
        if self.task:
            self.task.cancel()
