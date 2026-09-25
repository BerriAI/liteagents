"""Owns the raw Anthropic-wire message list alongside the typed message view."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from typing import Any

from ..types import (
    AssistantMessage,
    ContentBlock,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from ._internal import adapter


class ConversationHistory:
    """Owns one Anthropic-messages-format list[dict] (what gets sent to
    litellm.anthropic_messages(messages=...)) and the parallel, typed
    list[Message] view that callers and ModelRouters see. This is the ONLY
    place in the package that stores raw wire-format dicts -- everywhere
    else works with liteagents.types dataclasses.
    """

    def __init__(self, messages: Sequence[Message] = ()) -> None:
        self._raw: list[dict[str, Any]] = []
        self.messages: list[Message] = []
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

    def raw(self) -> list[dict[str, Any]]:
        return self._raw

    def add_user_text(self, text: str) -> UserMessage:
        self._raw.append({"role": "user", "content": text})
        message = UserMessage(content=text)
        self.messages.append(message)
        return message

    def add_user_tool_results(self, results: list[dict[str, Any]]) -> UserMessage:
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
        self._raw.append({"role": "assistant", "content": content_dicts})
        blocks = adapter.dicts_to_content_blocks(content_dicts)
        message = AssistantMessage(content=blocks, model=model)
        self.messages.append(message)
        return message
