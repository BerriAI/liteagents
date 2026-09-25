"""Provider protocol decoding for native-process operation recording."""

from __future__ import annotations

import json
import re
from typing import Any

from ..errors import ConfigurationError


def canonical(value: Any) -> Any:
    """Strip transport identity, preserving prompt content and tool arguments."""
    if isinstance(value, dict):
        result = {
            k: v
            for k, v in value.items()
            if k
            not in (
                "metadata",
                "client_metadata",
                "prompt_cache_key",
                "stream_options",
                "store",
                "service_tier",
            )
        }
        if isinstance(result.get("input"), list):
            result["input"] = [
                {k: v for k, v in item.items() if k != "id"}
                if isinstance(item, dict)
                and item.get("type") in ("message", "function_call_output")
                else item
                for item in result["input"]
            ]
            for item in result["input"]:
                if not isinstance(item, dict) or item.get("type") != "function_call_output":
                    continue
                output = item.get("output")
                if isinstance(output, list) and output and isinstance(output[0], dict):
                    text = output[0].get("text", "")
                    if re.fullmatch(r"Wall time: [0-9.]+ seconds\nOutput:", text):
                        item["output"] = [{**output[0], "text": "Output:"}, *output[1:]]
        return result
    return value


def tool_name(definition: dict[str, Any]) -> str:
    return definition.get("name", definition.get("function", {}).get("name", ""))


def select_definitions(definitions: list[dict[str, Any]], names: set[str]) -> list[dict[str, Any]]:
    selected = []
    for definition in definitions:
        if definition.get("type") == "namespace" and definition.get("name") == "mcp__liteagents":
            selected.append(
                {**definition, "tools": [t for t in definition["tools"] if tool_name(t) in names]}
            )
        elif managed_name(tool_name(definition), names):
            selected.append(definition)
    return selected


def managed_name(name: str, names: set[str]) -> str | None:
    for prefix in ("mcp__liteagents__", "liteagents_", "mcp__liteagents.", "liteagents."):
        if name.startswith(prefix) and name[len(prefix) :] in names:
            return name[len(prefix) :]
    return None


class ResponseRecorder:
    def __init__(self):
        self.calls: dict[str, dict[str, Any]] = {}
        self.complete = False

    def read(self, event: dict[str, Any]) -> str:
        kind = event.get("type", "")
        text = ""
        if kind == "message_start":
            self.json_response(event.get("message", {}))
        elif kind == "content_block_start":
            block = event["content_block"]
            if block["type"] == "tool_use":
                self.calls[str(event["index"])] = {
                    "id": block["id"],
                    "name": block["name"],
                    "arguments": "",
                    "input": block.get("input", {}),
                }
            elif block["type"] == "text":
                text = block.get("text", "")
        elif kind == "content_block_delta":
            delta = event["delta"]
            text = delta.get("text", "")
            if delta.get("type") == "input_json_delta":
                self.calls[str(event["index"])]["arguments"] += delta.get("partial_json", "")
        elif kind == "message_stop":
            self.complete = True
        elif kind == "response.output_text.delta":
            text = event.get("delta", "")
        elif kind == "response.completed":
            self.json_response(event["response"])
            self.complete = True
        elif kind in ("error", "response.failed", "response.incomplete"):
            raise ConnectionError("Provider stream did not complete successfully")
        for choice in event.get("choices", []):
            delta = choice.get("delta", {})
            text += delta.get("content") or ""
            for call in delta.get("tool_calls", []):
                item = self.calls.setdefault(
                    str(call["index"]), {"id": "", "name": "", "arguments": ""}
                )
                if call.get("id"):
                    item["id"] = call["id"]
                function = call.get("function", {})
                item["name"] += function.get("name", "")
                item["arguments"] += function.get("arguments", "")
            if choice.get("finish_reason"):
                self.complete = True
        return text

    def json_response(self, response: dict[str, Any]) -> None:
        for block in response.get("content", []):
            if block.get("type") == "tool_use":
                self.calls[block["id"]] = {
                    "id": block["id"],
                    "name": block["name"],
                    "input": block["input"],
                }
        for item in response.get("output", []):
            if item.get("type") == "function_call":
                self.calls[item["call_id"]] = {
                    "id": item["call_id"],
                    "name": (item["namespace"] + "." if item.get("namespace") else "")
                    + item["name"],
                    "arguments": item["arguments"],
                }
            elif item.get("type", "").endswith("_call"):
                raise ConfigurationError("Durable native runs only allow managed MCP tool calls")
        for choice in response.get("choices", []):
            for item in choice.get("message", {}).get("tool_calls", []):
                self.calls[item["id"]] = {"id": item["id"], **item["function"]}

    def results(self) -> list[dict[str, Any]]:
        calls = []
        seen = set()
        for item in self.calls.values():
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            arguments = item.get("arguments")
            calls.append(
                {
                    "id": item["id"],
                    "name": item["name"],
                    "input": json.loads(arguments) if arguments else item.get("input", {}),
                }
            )
        return calls


def sse_payload(frame: str) -> dict[str, Any] | None:
    data = "\n".join(line[5:].lstrip() for line in frame.splitlines() if line.startswith("data:"))
    return json.loads(data) if data and data != "[DONE]" else None


def split_frames(buffer: str) -> tuple[list[str], str]:
    parts = re.split(r"\r?\n\r?\n", buffer)
    return parts[:-1], parts[-1]
