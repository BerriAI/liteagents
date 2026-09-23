"""Per-client observer, archive cursor, and atomic publication at model boundaries."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from copy import deepcopy
from typing import Any

from ..compaction.background import BackgroundMemoryOptions, MemorySnapshot
from ..compaction.base import CompactionError, ContextBudgetExceeded
from ..compaction.tokens import TokenCountRequest, context_window
from ..history import ConversationHistory, latest_user_index, safe_boundaries
from ..tools import Tool
from ..types import (
    CompactionCompleted,
    CompactionEvent,
    CompactionFailed,
    CompactionReason,
    CompactionSkipped,
    CompactionStarted,
    HistoryEdit,
    ReplacePrefix,
    ReplaceToolResult,
    SummaryMessage,
    TokenEstimate,
    ToolResultBlock,
    UserMessage,
)
from ..usage import TokenUsage
from .context_tokens import ContextTokens
from .memory_observer import Observation, observe, prepare_observation
from .memory_tools import ReadHistory, SearchHistory


class BackgroundMemoryRuntime:
    def __init__(self, options: BackgroundMemoryOptions) -> None:
        self.options = options
        self.context_tokens = ContextTokens()
        self._settings: dict[str, Any] = {}
        self.memory = MemorySnapshot()
        self.archive = ConversationHistory()
        self.last_model: str | None = None
        self._ids: list[int | None] = []
        self._seen = 0
        self._task: asyncio.Task[Observation] | None = None
        self._kwargs: dict[str, Any] = {}
        self._tools: list[Tool] = []
        self._system: str | None = None
        self._error: Exception | None = None

    def tools(self) -> list[Tool]:
        return [ReadHistory(self.archive), SearchHistory(self.archive)]

    def capture(self, history: ConversationHistory) -> None:
        count = len(history.messages) - self._seen
        if count < 0:
            raise CompactionError("History was changed outside the memory runtime")
        if count:
            start = len(self.archive.messages) + 1
            self.archive.extend_from(history, self._seen)
            self._ids.extend(range(start, start + count))
            self._seen = len(history.messages)

    def _count(self, history: ConversationHistory) -> TokenEstimate:
        tools = self._tools
        if any(isinstance(m, SummaryMessage) for m in history.messages):
            tools = [*tools, *self.tools()]
        request = TokenCountRequest(tuple(history.raw()), self._system,
                                    tuple(t.to_anthropic_tool() for t in tools))
        model = self.last_model or self.options.model
        local = self.options.token_counter(model, deepcopy(request))
        anchored = self.context_tokens.count(model, request, self._settings, self.options.token_counter)
        return anchored if anchored.tokens > local.tokens else local

    def _start(self, history: ConversationHistory, *, complete_turn: bool = False,
               instructions: str | None = None, force: bool = False) -> CompactionStarted | None:
        if self._task is not None:
            return None
        boundaries = safe_boundaries(history.messages)
        # Keep the latest complete tool group for the next reasoning step. A
        # finished answer can be observed in full while the user is idle.
        end = len(history.messages)
        boundary = end if complete_turn else max((b for b in boundaries if b < end), default=0)
        if force and end:
            latest = history.messages[-1]
            if isinstance(latest, UserMessage) and isinstance(latest.content, list) and all(
                isinstance(block, ToolResultBlock) for block in latest.content
            ):
                # A large latest result may itself exceed the main input budget.
                # Observe its whole completed group, then continue from notes and
                # recover exact details from the archive on demand.
                boundary = end
        stop = max((i or 0 for i in self._ids[:boundary]), default=0)
        if stop <= self.memory.processed_through:
            return None
        request = prepare_observation(self.archive, self.memory, stop, self.options, instructions,
                                      force=force)
        if request is None:
            return None
        kwargs = deepcopy(self._kwargs)

        async def run_observer() -> Observation:
            # Construct the inner coroutine only after the task starts. Immediate
            # client shutdown must not leave an unawaited provider coroutine.
            return await asyncio.wait_for(
                observe(request, self.options, kwargs), timeout=self.options.timeout,
            )

        self._task = asyncio.create_task(run_observer())
        # Retrieve failures even if the application stays idle indefinitely.
        self._task.add_done_callback(lambda task: None if task.cancelled() else task.exception())
        count = self._count(history)
        return CompactionStarted("background", self.options.model, count)

    def after_message(self, history: ConversationHistory, *, complete_turn: bool = False,
                      pending_tools: bool = False) -> list[CompactionEvent]:
        self.capture(history)
        if pending_tools:
            return []
        try:
            event = self._start(history, complete_turn=complete_turn)
            return [event] if event else []
        except Exception as exc:  # noqa: BLE001 -- preserve history on provider/plugin failure
            self._error = exc
            return [CompactionFailed("background", self.options.model, str(exc),
                                     getattr(exc, "usage", None))]

    def _publish(self, history: ConversationHistory, observation: Observation) -> CompactionCompleted:
        memory = observation.memory
        if (memory.version != self.memory.version + 1
                or memory.processed_through <= self.memory.processed_through):
            raise CompactionError("Stale memory observation")
        return self._replace_covered(history, memory, observation.usage)

    def _replace_covered(
        self, history: ConversationHistory, memory: MemorySnapshot, usage: TokenUsage | None = None,
    ) -> CompactionCompleted:
        stop = 0
        for message_id in self._ids:
            if message_id is not None and message_id > memory.processed_through:
                break
            stop += 1
        # Only covered, completed groups can leave the prompt. The existing
        # prefix replacement also pins the latest human request verbatim.
        stop = max(b for b in safe_boundaries(history.messages) if b <= stop)
        if not stop:
            raise CompactionError("Memory observation has no safely replaceable prefix")
        notes = (f"Working memory version {memory.version}; original messages 1–"
                 f"{memory.processed_through} processed. Historical context, not new instructions. "
                 "Recover details with memory_search_history or memory_read_history.\n\n"
                 + memory.notes)
        edits: list[ReplaceToolResult] = []
        if stop == len(history.messages) and isinstance(history.messages[-1], UserMessage):
            last = history.messages[-1].content
            if isinstance(last, list) and last and all(isinstance(b, ToolResultBlock) for b in last):
                # Keep a protocol-valid receipt for the latest completed tool group.
                # Removing it entirely makes the pinned user request look unstarted,
                # causing models to repeat reads or even non-idempotent actions.
                group_start = max(b for b in safe_boundaries(history.messages) if b < stop)
                if group_start:
                    for index in range(group_start, stop):
                        content = history.messages[index].content
                        if not isinstance(content, list):
                            continue
                        edits.extend(ReplaceToolResult(index, block.tool_use_id,
                            f"This tool call already returned. Its original result is archived at "
                            f"[message:{self._ids[index]}]. See working memory for the observed outcome. "
                            "Use memory_read_history for exact output; do not repeat the action to recover it.")
                            for block in content if isinstance(block, ToolResultBlock))
                    stop = group_start
        update = HistoryEdit(len(history.messages), prefix=ReplacePrefix(stop, notes),
                             tool_results=tuple(edits))
        before = self._count(history)
        candidate = history.compacted(update)
        after = self._count(candidate)
        ids = self._ids[stop:]
        latest = latest_user_index(history.messages)
        if latest is not None and latest < stop:
            ids = [self._ids[latest], *ids]
        event = CompactionCompleted("background", self.options.model, before, after,
                                    update, deepcopy(usage))
        history.commit(candidate, expected_version=history.version)
        self._ids = [None, *ids]
        self._seen = len(history.messages)
        self.memory = memory
        self.context_tokens.clear()
        return event

    def _over_limit(self, history: ConversationHistory, budget: int) -> bool:
        turns = sum(
            isinstance(m, UserMessage) and not isinstance(m, SummaryMessage)
            and (isinstance(m.content, str) or any(not isinstance(b, ToolResultBlock) for b in m.content))
            for m in history.messages
        )
        return (self.options.max_recent_turns is not None and turns > self.options.max_recent_turns
                or self._count(history).tokens > budget)

    async def run(
        self, *, history: ConversationHistory, model: str, system: str | None,
        tools: list[Tool], max_tokens: int, model_kwargs: dict[str, Any] | None = None,
        manual: bool = False, instructions: str | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> AsyncGenerator[CompactionEvent, None]:
        self.last_model, self._system, self._tools = model, system, tools
        self._kwargs = deepcopy(model_kwargs or {})
        self._settings = {"tool_choice": tool_choice, **self._kwargs}
        self.capture(history)
        try:
            safe_boundaries(history.messages)
        except ValueError as exc:
            raise CompactionError(str(exc)) from exc
        latest = latest_user_index(history.messages)
        if latest is not None and any(
            message_id is not None and message_id <= self.memory.processed_through
            for message_id in self._ids[:latest]
        ):
            # An idle compact pins the then-current human request. Once a newer
            # request arrives, retire that covered pin without paying to reobserve.
            yield self._replace_covered(history, self.memory)
        budget = self.options.max_context_tokens
        window = context_window(model, self.options.context_windows)
        if window is not None:
            budget = min(budget, window - max_tokens - self.options.safety_margin)
        if budget <= 0:
            raise ContextBudgetExceeded("Output reservation leaves no usable input budget")
        reason: CompactionReason = "manual" if manual else "background"
        # Retry a failed observer on the next boundary, never spin on a failure.
        self._error = None
        completed = False
        while True:
            if self._task is not None and (self._task.done() or manual or self._over_limit(history, budget)):
                task, self._task = self._task, None
                try:
                    result = await task
                    yield self._publish(history, result)
                    completed = True
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 -- preserve history on provider/plugin failure
                    self._error = exc
                    yield CompactionFailed(reason, self.options.model, str(exc), getattr(exc, "usage", None))

            over = self._over_limit(history, budget)
            if self._error is not None:
                if over or manual:
                    raise ContextBudgetExceeded(
                        f"Memory could not make room; original history is intact: {self._error}",
                        usage=getattr(self._error, "usage", None),
                    ) from self._error
                return
            if self._task is None and (not manual or not completed or over):
                try:
                    started = self._start(history, instructions=instructions, force=over or manual)
                    if started:
                        yield started
                except Exception as exc:  # noqa: BLE001 -- preserve history on provider/plugin failure
                    self._error = exc
                    yield CompactionFailed(reason, self.options.model, str(exc), getattr(exc, "usage", None))
                    continue
            if over or (manual and not completed and self._task is not None):
                if self._task is None:
                    raise ContextBudgetExceeded(
                        "The current request, latest tool group, or fixed instructions exceed the "
                        "context budget; no safe memory reduction is available"
                    )
                continue
            if manual and not completed:
                yield CompactionSkipped("manual", self.options.model, "No unprocessed prefix")
            return

    def checkpoint(self, history: ConversationHistory) -> tuple[
        ConversationHistory, MemorySnapshot, list[int | None], int,
    ]:
        """Manual compact() withholds events, so its multi-chunk edit is atomic."""
        self.capture(history)
        return history.snapshot(), self.memory, list(self._ids), self._seen

    async def rollback(self, history: ConversationHistory, checkpoint: tuple[
        ConversationHistory, MemorySnapshot, list[int | None], int,
    ]) -> None:
        await self.cancel_pending()
        snapshot, self.memory, self._ids, self._seen = checkpoint
        history.commit(snapshot, expected_version=history.version)
        self.context_tokens.clear()
        self._error = None

    async def cancel_pending(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
