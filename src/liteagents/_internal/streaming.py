"""Assemble Anthropic SSE into complete responses while emitting display deltas."""

from __future__ import annotations

import codecs
import inspect
import json
from collections.abc import AsyncGenerator
from typing import Any

from ..types import TextDelta


async def _events(stream: Any) -> AsyncGenerator[dict[str, Any], None]:
    decoder = codecs.getincrementaldecoder("utf-8")()
    buffer = ""
    async for chunk in stream:
        if isinstance(chunk, dict):
            yield chunk
            continue
        if hasattr(chunk, "model_dump"):
            yield chunk.model_dump()
            continue
        buffer += decoder.decode(chunk) if isinstance(chunk, bytes) else chunk
        # Split only complete SSE records. Chunks can contain several events
        # or end halfway through a JSON string or a UTF-8 code point.
        buffer = buffer.replace("\r\n", "\n")
        while "\n\n" in buffer:
            record, buffer = buffer.split("\n\n", 1)
            data = "\n".join(line[5:].lstrip() for line in record.splitlines()
                             if line.startswith("data:"))
            if data and data != "[DONE]":
                yield json.loads(data)
    buffer += decoder.decode(b"", final=True)
    if buffer.strip():
        raise ValueError("Incomplete SSE event from model")


async def stream_response(stream: Any, *, model: str) -> AsyncGenerator[TextDelta | dict[str, Any], None]:
    """Close the upstream stream on completion, failure, cancellation or aclose()."""
    response: dict[str, Any] = {"model": model, "usage": {}, "content": []}
    blocks: dict[int, dict[str, Any]] = {}
    arguments: dict[int, str] = {}
    completed = False
    events = _events(stream)
    try:
        async for event in events:
            kind = event.get("type")
            if kind == "error":
                raise RuntimeError("Model returned a streaming error")
            if kind == "message_start":
                response.update(event["message"])
                response["usage"] = dict(response.get("usage") or {})
            elif kind == "content_block_start":
                index = event["index"]
                blocks[index] = dict(event["content_block"])
                if blocks[index].get("type") == "text" and blocks[index].get("text"):
                    yield TextDelta(blocks[index]["text"], response["model"])
            elif kind == "content_block_delta":
                index, delta = event["index"], event["delta"]
                block = blocks[index]
                if delta["type"] == "text_delta":
                    block["text"] = block.get("text", "") + delta["text"]
                    yield TextDelta(delta["text"], response["model"])
                elif delta["type"] == "input_json_delta":
                    arguments[index] = arguments.get(index, "") + delta["partial_json"]
                elif delta["type"] == "thinking_delta":
                    block["thinking"] = block.get("thinking", "") + delta["thinking"]
                elif delta["type"] == "signature_delta":
                    block["signature"] = block.get("signature", "") + delta["signature"]
            elif kind == "content_block_stop":
                index = event["index"]
                if index in arguments:
                    blocks[index]["input"] = json.loads(arguments.pop(index))
            elif kind == "message_delta":
                response.update(event.get("delta", {}))
                response["usage"].update(event.get("usage") or {})
            elif kind == "message_stop":
                if arguments:
                    raise ValueError("Incomplete tool arguments from model")
                response["content"] = [blocks[index] for index in sorted(blocks)]
                completed = True
                break
        if not completed:
            raise ValueError("Model stream ended before message_stop")
    finally:
        await events.aclose()
        close = getattr(stream, "aclose", None) or getattr(stream, "close", None)
        if close is not None:
            result = close()
            if inspect.isawaitable(result):
                await result
    yield response
