"""The only place that understands litellm.anthropic_messages()'s wire shapes.

litellm.anthropic_messages() returns dict-like or attribute-like objects
depending on the code path, and its content blocks are sometimes plain
dicts and sometimes pydantic BaseModel instances. Every function here
normalizes those shapes into plain dicts (or our public dataclasses), so
the rest of liteagents never has to guess which shape it got.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from typing import Any

from ..types import ContentBlock, TextBlock, ToolUseBlock


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def content_blocks_to_dicts(response_content: list) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for block in response_content:
        block_type = _get(block, "type")
        if block_type == "text":
            blocks.append({"type": "text", "text": _get(block, "text")})
        elif block_type == "tool_use":
            blocks.append(
                {
                    "type": "tool_use",
                    "id": _get(block, "id"),
                    "name": _get(block, "name"),
                    "input": _get(block, "input"),
                }
            )
        else:
            # Unknown/future block types (e.g. thinking, redacted_thinking) --
            # pass through verbatim so history round-trips correctly even for
            # block types liteagents doesn't model with a public dataclass yet.
            if isinstance(block, dict):
                blocks.append(block)
            else:
                blocks.append(dict(getattr(block, "__dict__", {}) or {"type": block_type}))
    return blocks


def dicts_to_content_blocks(raw: list[dict[str, Any]]) -> list[ContentBlock]:
    blocks: list[ContentBlock] = []
    for block in raw:
        block_type = block.get("type")
        if block_type == "text":
            blocks.append(TextBlock(text=block.get("text", "")))
        elif block_type == "tool_use":
            blocks.append(
                ToolUseBlock(
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    input=block.get("input", {}),
                )
            )
        # Other types (e.g. thinking) are skipped rather than raised on --
        # liteagents has no public dataclass for them yet, and this function's
        # job is producing the *typed* view, not the raw history (which keeps
        # them via content_blocks_to_dicts()).
    return blocks


def extract_response_fields(
    response: Any,
) -> tuple[list[dict[str, Any]], str | None, str | None, dict[str, Any] | None]:
    content_as_dicts = content_blocks_to_dicts(_get(response, "content", []))
    stop_reason = _get(response, "stop_reason")
    model = _get(response, "model")
    usage = _get(response, "usage")
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    return content_as_dicts, stop_reason, model, usage


def tool_result_block(tool_use_id: str, content: str | list[dict[str, Any]],
                      is_error: bool = False) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
        "is_error": is_error,
    }


def parse_sse_events(raw_lines: Iterable[bytes | str]) -> Iterator[dict[str, Any]]:
    # litellm's anthropic_messages(stream=True) yields raw SSE bytes, not
    # typed objects, so we parse the wire format ourselves: blank-line
    # delimited blocks, each with an "event:" and a "data:" line.
    event_name: str | None = None
    data_lines: list[str] = []

    def _decode(line: bytes | str) -> str:
        return line.decode("utf-8") if isinstance(line, bytes) else line

    for line in raw_lines:
        text = _decode(line).rstrip("\n").rstrip("\r")
        if text == "":
            if event_name is not None and data_lines:
                try:
                    data = json.loads("".join(data_lines))
                except json.JSONDecodeError:
                    event_name, data_lines = None, []
                    continue
                yield {"event": event_name, "data": data}
            event_name, data_lines = None, []
            continue
        if text.startswith("event:"):
            event_name = text[len("event:") :].strip()
        elif text.startswith("data:"):
            data_lines.append(text[len("data:") :].strip())

    if event_name is not None and data_lines:
        try:
            data = json.loads("".join(data_lines))
        except json.JSONDecodeError:
            return
        yield {"event": event_name, "data": data}
