"""Adapt tools from a caller-owned, initialized MCP ClientSession.

The application owns transport, authentication and session lifetime. This
works with stdio, Streamable HTTP, SSE and in-memory sessions without making
the agent itself a subprocess manager or an MCP server.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Collection
from typing import TYPE_CHECKING, Any

from .tools import Tool

if TYPE_CHECKING:
    from mcp import ClientSession


class MCPTool(Tool):
    def __init__(self, session: ClientSession, definition: dict[str, Any], *, prefix: str = ""):
        self._session = session
        self._remote_name = definition["name"]
        self.name = prefix + self._remote_name
        self.description = definition.get("description") or ""
        self.input_schema = definition["inputSchema"]

    async def execute(self, input: dict[str, Any]) -> str | list[dict[str, Any]]:
        result = await self._session.call_tool(self._remote_name, input)
        wire = result.model_dump(mode="json", by_alias=True, exclude_none=True)
        content: list[dict[str, Any]] = []
        for block in wire.get("content", []):
            if block["type"] == "text":
                content.append({"type": "text", "text": block["text"]})
            elif block["type"] == "image":
                content.append({"type": "image", "source": {
                    "type": "base64", "media_type": block["mimeType"], "data": block["data"],
                }})
            else:
                # Preserve resource links, embedded resources and other MCP
                # blocks as data rather than dropping returned evidence.
                content.append({"type": "text", "text": json.dumps(block, ensure_ascii=False)})
        if wire.get("structuredContent") is not None:
            content.append({"type": "text", "text": json.dumps(wire["structuredContent"], ensure_ascii=False)})
        if wire.get("isError"):
            raise RuntimeError("MCP tool failed: " + json.dumps(content, ensure_ascii=False))
        return content or ""


async def load_mcp_tools(
    session: ClientSession, *, allowed_tool_names: Collection[str] | None = None, prefix: str = "",
) -> list[Tool]:
    """Discover all pages, filtering remote names before exposing executable tools.

    None exposes all tools; an empty collection exposes none. Optional prefixes
    disambiguate names when using several servers. Keep the session open for the
    entire agent run. Re-run discovery explicitly when the tool catalog changes.
    """
    from mcp.types import PaginatedRequestParams

    allowed = None if allowed_tool_names is None else set(allowed_tool_names)
    tools: list[Tool] = []
    cursor = None
    seen_cursors: set[str] = set()
    seen_names: set[str] = set()
    # MCP 1.x used a cursor argument; MCP 2.x accepts the request params.
    legacy_cursor = "cursor" in inspect.signature(session.list_tools).parameters
    while True:
        list_kwargs: dict[str, Any] = ({"cursor": cursor} if legacy_cursor else
                                       {"params": PaginatedRequestParams(cursor=cursor)})
        result = await session.list_tools(**list_kwargs)
        page = result.model_dump(mode="json", by_alias=True, exclude_none=True)
        for definition in page["tools"]:
            name = definition["name"]
            if allowed is not None and name not in allowed:
                continue
            if name in seen_names:
                raise ValueError(f"Duplicate MCP tool name: {name}")
            seen_names.add(name)
            tools.append(MCPTool(session, definition, prefix=prefix))
        cursor = page.get("nextCursor")
        if cursor is None:
            return tools
        if cursor in seen_cursors:
            raise ValueError("MCP tool listing repeated a pagination cursor")
        seen_cursors.add(cursor)
