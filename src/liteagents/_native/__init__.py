"""Optional Rust-accelerated helpers. Falls back to pure Python when the
compiled extension isn't available -- liteagents must install and run
correctly with no Rust toolchain present; this module is a pure accelerator,
never a hard dependency.
"""

from __future__ import annotations

from typing import Any

try:
    from . import _liteagents_native as _native  # type: ignore[attr-defined]

    _HAS_NATIVE = True
except ImportError:
    _native = None
    _HAS_NATIVE = False


def validate_tool_input(input_schema: dict[str, Any], input: dict[str, Any]) -> bool:
    if _HAS_NATIVE:
        return _native.validate_tool_input(input_schema, input)
    return _validate_tool_input_py(input_schema, input)


def _validate_tool_input_py(input_schema: dict[str, Any], input: dict[str, Any]) -> bool:
    for field in input_schema.get("required", []):
        if field not in input:
            raise ValueError(f"missing required field: {field!r}")
    # bool precedes int/number because Python bool is an int subclass --
    # isinstance(True, int) is True, which would wrongly pass "integer"/"number".
    type_map: dict[str, Any] = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    for field, spec in input_schema.get("properties", {}).items():
        if field not in input:
            continue
        expected_type = spec.get("type")
        value = input[field]
        if expected_type == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"field {field!r} expected type {expected_type!r}")
            continue
        if expected_type in ("integer", "number") and isinstance(value, bool):
            raise ValueError(f"field {field!r} expected type {expected_type!r}")
        expected = type_map.get(expected_type)
        if expected and not isinstance(value, expected):
            raise ValueError(f"field {field!r} expected type {expected_type!r}")
    return True


def normalize_content_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if _HAS_NATIVE:
        return _native.normalize_content_blocks(blocks)
    return _normalize_content_blocks_py(blocks)


def _normalize_content_blocks_py(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for block in blocks:
        if not isinstance(block, dict):
            raise ValueError(f"content block must be a dict, got {block!r}")
        if "type" not in block:
            raise ValueError(f"content block missing 'type': {block!r}")
        if not isinstance(block["type"], str):
            raise ValueError(f"content block 'type' must be a string, got {block['type']!r}")
    return blocks


def append_history_entry(raw_history_json: str, entry_json: str) -> str:
    if _HAS_NATIVE:
        return _native.append_history_entry(raw_history_json, entry_json)
    # No native fast path here is fine -- callers with small histories can
    # just use a plain Python list.append instead of this JSON round-trip.
    import json

    history = json.loads(raw_history_json)
    entry = json.loads(entry_json)
    if not isinstance(history, list):
        raise ValueError("raw_history_json must decode to a JSON array")
    if not isinstance(entry, dict):
        raise ValueError("entry_json must decode to a JSON object")
    history.append(entry)
    return json.dumps(history)
