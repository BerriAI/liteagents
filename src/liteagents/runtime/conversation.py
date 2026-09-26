"""Lossless shared conversation messages, seeded into each native protocol."""

from __future__ import annotations

import json

from ..runs import RunResult
from ..types import AssistantMessage, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage
from .serialization import dump_result, load_result


def dump_history(messages):
    return dump_result(RunResult("history", "shared", messages))["messages"]


def load_history(messages):
    return load_result({"run_id": "history", "harness": "shared", "messages": messages}).messages


def chat_history(messages):
    result = []
    for message in messages:
        if isinstance(message.content, str):
            result.append({"role": "user", "content": message.content})
        elif isinstance(message, AssistantMessage):
            item = {
                "role": "assistant",
                "content": "".join(b.text for b in message.content if isinstance(b, TextBlock))
                or None,
            }
            calls = [
                {
                    "id": b.id,
                    "type": "function",
                    "function": {"name": b.name, "arguments": json.dumps(b.input)},
                }
                for b in message.content
                if isinstance(b, ToolUseBlock)
            ]
            if calls:
                item["tool_calls"] = calls
            result.append(item)
        elif isinstance(message, UserMessage):
            for block in message.content:
                if isinstance(block, ToolResultBlock):
                    result.append(
                        {
                            "role": "tool",
                            "tool_call_id": block.tool_use_id,
                            "content": block.content or "",
                        }
                    )
                elif isinstance(block, TextBlock):
                    result.append({"role": "user", "content": block.text})
    return result


def langchain_history(messages):
    from langchain_core.messages import convert_to_messages

    result = convert_to_messages(chat_history(messages))
    for i, message in enumerate(result):
        message.id = f"liteagents-history-{i}"
    return result


def pydantic_history(messages):
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    names = {}
    result = []
    for message in messages:
        if isinstance(message.content, str):
            result.append(ModelRequest(parts=[UserPromptPart(message.content)]))
        elif isinstance(message, AssistantMessage):
            parts = []
            for block in message.content:
                if isinstance(block, TextBlock):
                    parts.append(TextPart(block.text))
                elif isinstance(block, ToolUseBlock):
                    names[block.id] = block.name
                    parts.append(ToolCallPart(block.name, block.input, block.id))
            result.append(ModelResponse(parts=parts))
        else:
            parts = []
            for block in message.content:
                if isinstance(block, TextBlock):
                    parts.append(UserPromptPart(block.text))
                elif isinstance(block, ToolResultBlock):
                    parts.append(
                        ToolReturnPart(names[block.tool_use_id], block.content, block.tool_use_id)
                    )
            result.append(ModelRequest(parts=parts))
    return result
