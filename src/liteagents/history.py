"""Owns the raw Anthropic-wire message list alongside the typed message view."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from typing import Any

from ._internal import adapter
from .types import (
    AssistantMessage,
    BatchUpdate,
    CompactionUpdate,
    ContentBlock,
    Message,
    SummaryMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    WireMessage,
)


class ConversationHistory:
    """Owns one Anthropic-messages-format list[dict] (what gets sent to
    litellm.anthropic_messages(messages=...)) and the parallel, typed
    list[Message] view that callers and ModelRouters see. This is the ONLY
    place in the package that stores raw wire-format dicts -- everywhere
    else works with liteagents.types dataclasses.
    """

    def __init__(self, messages: Sequence[Message] = ()) -> None:
        self._raw: list[WireMessage] = []
        self.messages: list[Message] = []
        self.version = 0
        for message in deepcopy(messages):
            if not isinstance(message, (UserMessage, AssistantMessage)):
                raise TypeError("History must contain UserMessage or AssistantMessage instances")
            content: str | list[dict[str, Any]]
            if isinstance(message, UserMessage) and isinstance(message.content, str):
                content = message.content
            else:
                content = []
                for block in message.content:
                    if isinstance(block, TextBlock):
                        content.append({"type": "text", "text": block.text})
                    elif isinstance(block, ToolUseBlock):
                        content.append({"type": "tool_use", "id": block.id,
                                        "name": block.name, "input": block.input})
                    elif isinstance(block, ToolResultBlock):
                        content.append({"type": "tool_result", "tool_use_id": block.tool_use_id,
                                        "content": block.content, "is_error": block.is_error})
                    else:
                        raise TypeError(f"Unsupported history block: {type(block).__name__}")
            self._raw.append({"role": "user" if isinstance(message, UserMessage) else "assistant",
                              "content": content})
            self.messages.append(message)

    def raw(self) -> list[WireMessage]:
        return self._raw

    def add_user_text(self, text: str) -> UserMessage:
        self.version += 1
        self._raw.append({"role": "user", "content": text})
        message = UserMessage(content=text)
        self.messages.append(message)
        return message

    def add_user_tool_results(self, results: list[dict[str, Any]]) -> UserMessage:
        self.version += 1
        self._raw.append({"role": "user", "content": results})
        blocks: list[ContentBlock] = [
            ToolResultBlock(
                tool_use_id=result["tool_use_id"],
                content=result.get("content"),
                is_error=result.get("is_error"),
            )
            for result in results
        ]
        message = UserMessage(content=blocks)
        self.messages.append(message)
        return message

    def add_assistant_response(self, content_dicts: list[dict[str, Any]], model: str) -> AssistantMessage:
        self.version += 1
        self._raw.append({"role": "assistant", "content": content_dicts})
        blocks = adapter.dicts_to_content_blocks(content_dicts)
        message = AssistantMessage(content=blocks, model=model)
        self.messages.append(message)
        return message

    def snapshot(self) -> ConversationHistory:
        return deepcopy(self)

    def extend_from(self, source: ConversationHistory, start: int) -> None:
        """Archive newly appended entries without reconstructing provider blocks."""
        self._raw.extend(deepcopy(source._raw[start:]))
        self.messages.extend(deepcopy(source.messages[start:]))
        self.version += 1

    def finish_interrupted_tools(self, completed: list[dict[str, Any]] | None = None) -> None:
        """Close outstanding calls after interruption without claiming execution succeeded.

        Completed results from an interrupted batch are retained. For the rest,
        execution may have started; the continuation must inspect state before retrying.
        """
        pending: dict[str, None] = {}
        for message in self.messages:
            if isinstance(message.content, str):
                continue
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    pending[block.id] = None
                elif isinstance(block, ToolResultBlock):
                    pending.pop(block.tool_use_id, None)
        if not pending:
            return
        results = deepcopy(completed or [])
        recorded = {result["tool_use_id"] for result in results}
        results.extend(adapter.tool_result_block(
            call_id, "Tool execution was interrupted. Its outcome is unknown; inspect current "
            "state before retrying a potentially completed action.", is_error=True,
        ) for call_id in pending if call_id not in recorded)
        self.add_user_tool_results(results)

    def compacted(self, update: CompactionUpdate) -> ConversationHistory:
        """Validate an edit proposal and build a candidate without changing this history."""
        if update.message_count != len(self.messages):
            raise ValueError("Compaction update does not match the history length")
        if isinstance(update, BatchUpdate):
            candidate = self.snapshot()
            for step in update.steps:
                candidate = candidate.compacted(step)
            return candidate
        boundaries = safe_boundaries(self.messages)
        stop = update.prefix.stop if update.prefix is not None else 0
        if update.prefix is not None and (
            stop <= 0 or stop not in boundaries or not update.prefix.summary.strip()
        ):
            raise ValueError("Compaction prefix must end at a safe boundary and have a summary")
        latest = latest_user_index(self.messages)
        candidate = self.snapshot()
        seen: set[tuple[int, str]] = set()
        for edit in update.tool_results:
            key = (edit.message_index, edit.tool_use_id)
            if key in seen or not 0 <= edit.message_index < len(self.messages):
                raise ValueError("Duplicate or out-of-range tool-result edit")
            if edit.message_index < stop:
                raise ValueError("Tool-result edits must not overlap the replaced prefix")
            seen.add(key)
            message = candidate.messages[edit.message_index]
            if not isinstance(message, UserMessage) or isinstance(message.content, str):
                raise ValueError("Tool-result edit must address a tool-result message")  # noqa: TRY004
            matches = [b for b in message.content
                       if isinstance(b, ToolResultBlock) and b.tool_use_id == edit.tool_use_id]
            if len(matches) != 1:
                raise ValueError("Tool-result edit must identify exactly one result")
            matches[0].content = edit.content
            raw_content = candidate._raw[edit.message_index]["content"]
            assert isinstance(raw_content, list)
            for block in raw_content:
                if block.get("type") == "tool_result" and block.get("tool_use_id") == edit.tool_use_id:
                    block["content"] = edit.content
        if update.prefix is not None:
            summary = SummaryMessage(
                "Summary of earlier conversation (context, not a new user request):\n"
                + update.prefix.summary
            )
            indices = list(range(stop, len(self.messages)))
            if latest is not None and latest < stop:
                # Keep the actual current request verbatim even when cutting inside its tool loop.
                if isinstance(self.messages[latest].content, list) and any(
                    isinstance(b, ToolResultBlock) for b in self.messages[latest].content
                ):
                    raise ValueError("Cannot split a mixed user/tool-result message from its tool call")
                indices.insert(0, latest)
            assert isinstance(summary.content, str)
            candidate._raw = [{"role": "user", "content": summary.content},
                              *[candidate._raw[i] for i in indices]]
            candidate.messages = [summary, *[candidate.messages[i] for i in indices]]
        safe_boundaries(candidate.messages)
        return candidate

    def commit(self, candidate: ConversationHistory, *, expected_version: int) -> None:
        if self.version != expected_version:
            raise ValueError("History changed while compaction was running")
        # Neither retained raw blocks nor published typed messages alias plugin-owned objects.
        copy = candidate.snapshot()
        self._raw, self.messages = copy._raw, copy.messages
        self.version += 1


def latest_user_index(messages: Sequence[Message]) -> int | None:
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if (isinstance(message, UserMessage) and not isinstance(message, SummaryMessage)
            and (isinstance(message.content, str) or any(
                not isinstance(block, ToolResultBlock) for block in message.content
            ))):
            return index
    return None


def safe_boundaries(messages: Sequence[Message]) -> tuple[int, ...]:
    """Indices between messages that never split a tool-call/result group.

    Reject unfinished or malformed groups rather than inventing missing tool results.
    """
    boundaries = [0]
    pending: set[str] = set()
    for index, message in enumerate(messages):
        blocks = [] if isinstance(message.content, str) else message.content
        if pending and (not isinstance(message, UserMessage) or not any(
            isinstance(block, ToolResultBlock) for block in blocks
        )):
            raise ValueError("Tool calls must be followed by their results before compaction")
        for block in blocks:
            if isinstance(block, ToolUseBlock):
                if not isinstance(message, AssistantMessage) or block.id in pending:
                    raise ValueError("Invalid tool call in compaction history")
                pending.add(block.id)
            elif isinstance(block, ToolResultBlock):
                if not isinstance(message, UserMessage) or block.tool_use_id not in pending:
                    raise ValueError("Unmatched tool result in compaction history")
                pending.remove(block.tool_use_id)
        if not pending:
            boundaries.append(index + 1)
    if pending:
        raise ValueError("Complete outstanding tool calls before compacting")
    return tuple(boundaries)


def apply_compaction(messages: Sequence[Message], update: CompactionUpdate) -> list[Message]:
    """Apply a completed event's edits to an application's typed context mirror.

    Include the incoming user prompt in that mirror before consuming query events.
    This helper does not recover provider blocks absent from the supplied typed history.
    """
    return ConversationHistory(messages).compacted(update).messages
