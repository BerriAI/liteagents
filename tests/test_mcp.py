import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult, ListToolsResult

from liteagents.legacy import LiteAgentOptions, ToolResultBlock, query
from liteagents.mcp import load_mcp_tools

from .conftest import text_response, tool_use_response


def listing(names, cursor=None):
    return ListToolsResult.model_validate({
        "tools": [{"name": name, "description": f"Run {name}", "inputSchema": {
            "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"],
        }} for name in names], "nextCursor": cursor,
    })


def session(pages, result=None):
    listing = AsyncMock(side_effect=pages)

    async def list_tools(*, params=None):
        return await listing(params=params)

    return SimpleNamespace(list_tools=list_tools, list_requests=listing,
                           call_tool=AsyncMock(return_value=result))


async def test_discovery_pages_allowlist_prefix_and_execution():
    result = CallToolResult.model_validate({"content": [{"type": "text", "text": "evidence"}]})
    client = session([listing(["write", "read"], "page2"), listing(["search"])], result)
    tools = await load_mcp_tools(client, allowed_tool_names={"read", "search"}, prefix="memory_")
    assert [tool.name for tool in tools] == ["memory_read", "memory_search"]
    assert tools[0].input_schema["required"] == ["query"]
    assert client.list_requests.call_args.kwargs["params"].cursor == "page2"
    assert await tools[0].execute({"query": "hello"}) == [{"type": "text", "text": "evidence"}]
    client.call_tool.assert_awaited_once_with("read", {"query": "hello"})


async def test_empty_allowlist_exposes_nothing():
    client = session([listing(["write"])])
    assert await load_mcp_tools(client, allowed_tool_names=[]) == []
    client.call_tool.assert_not_called()


@pytest.mark.parametrize("pages,match", [
    ([listing(["read"], "x"), listing(["read"])], "Duplicate"),
    ([listing(["read"], "x"), listing(["search"], "x")], "pagination"),
])
async def test_invalid_catalog_fails_without_looping(pages, match):
    with pytest.raises(ValueError, match=match):
        await load_mcp_tools(session(pages))


async def test_mcp_error_becomes_tool_error_and_model_can_recover(mock_anthropic_messages):
    result = CallToolResult.model_validate({"isError": True, "content": [{"type": "text", "text": "not found"}]})
    tools = await load_mcp_tools(session([listing(["read"])], result))
    mock_anthropic_messages.push(tool_use_response(tool_use_id="1", name="read", input={"query": "missing"}, model="test"))
    mock_anthropic_messages.push(text_response("No evidence found", model="test"))
    messages = [m async for m in query(prompt="search", options=LiteAgentOptions(model="test", tools=tools))]
    result = messages[1].content[0]
    assert isinstance(result, ToolResultBlock) and result.is_error
    assert "not found" in result.content


async def test_structured_images_and_resources_preserved():
    result = CallToolResult.model_validate({"content": [
        {"type": "image", "mimeType": "image/png", "data": "aGVsbG8="},
        {"type": "resource", "resource": {"uri": "memory://one", "text": "source evidence"}},
    ], "structuredContent": {"found": True}})
    tools = await load_mcp_tools(session([listing(["read"])], result))
    blocks = await tools[0].execute({"query": "one"})
    assert blocks[0] == {"type": "image", "source": {
        "type": "base64", "media_type": "image/png", "data": "aGVsbG8=",
    }}
    assert json.loads(blocks[1]["text"])["resource"]["text"] == "source evidence"
    assert json.loads(blocks[2]["text"]) == {"found": True}


async def test_cancelled_mcp_call_is_not_swallowed(mock_anthropic_messages):
    client = session([listing(["read"])])
    client.call_tool.side_effect = asyncio.CancelledError
    tools = await load_mcp_tools(client)
    mock_anthropic_messages.push(tool_use_response(tool_use_id="1", name="read", input={"query": "q"}, model="test"))
    with pytest.raises(asyncio.CancelledError):
        _ = [m async for m in query(prompt="q", options=LiteAgentOptions(model="test", tools=tools))]


async def test_filtered_tool_cannot_be_dispatched(mock_anthropic_messages):
    client = session([listing(["read", "write"])])
    tools = await load_mcp_tools(client, allowed_tool_names=["read"])
    mock_anthropic_messages.push(tool_use_response(tool_use_id="1", name="write", input={}, model="test"))
    mock_anthropic_messages.push(text_response("Cannot write", model="test"))
    messages = [m async for m in query(prompt="write", options=LiteAgentOptions(model="test", tools=tools))]
    assert messages[1].content[0].is_error
    client.call_tool.assert_not_called()


async def test_duplicate_tools_across_servers_fail_before_model_call(mock_anthropic_messages):
    one = await load_mcp_tools(session([listing(["read"])]))
    two = await load_mcp_tools(session([listing(["read"])]))
    with pytest.raises(ValueError, match="unique"):
        _ = [m async for m in query(prompt="q", options=LiteAgentOptions(model="test", tools=one + two))]
    assert not mock_anthropic_messages.calls
