from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..runs import RunResult
from ..types import AssistantMessage, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage


def dump_result(result: RunResult) -> dict[str, Any]:
    messages = []
    for message in result.messages:
        value = asdict(message)
        value["kind"] = "assistant" if isinstance(message, AssistantMessage) else "user"
        if not isinstance(message.content, str):
            value["content"] = [
                {"kind": type(block).__name__, **asdict(block)} for block in message.content
            ]
        messages.append(value)
    return {
        "run_id": result.run_id,
        "harness": result.harness,
        "messages": messages,
        "session_id": result.session_id,
        "usage": result.usage,
    }


def load_result(value: dict[str, Any]) -> RunResult:
    constructors = {
        "TextBlock": TextBlock,
        "ToolUseBlock": ToolUseBlock,
        "ToolResultBlock": ToolResultBlock,
    }
    messages = []
    for raw in value["messages"]:
        message = dict(raw)
        kind = message.pop("kind")
        if not isinstance(message["content"], str):
            blocks = []
            for raw_block in message["content"]:
                block = dict(raw_block)
                constructor = constructors[block.pop("kind")]
                blocks.append(constructor(**block))
            message["content"] = blocks
        messages.append((AssistantMessage if kind == "assistant" else UserMessage)(**message))
    return RunResult(
        value["run_id"], value["harness"], messages, value.get("session_id"), value.get("usage", {})
    )
