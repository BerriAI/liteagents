import asyncio
import json
import socket
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from liteagents import LiteAgentOptions, query
from liteagents.mcp import load_mcp_tools

from .conftest import text_response, tool_use_response

SERVER = Path(__file__).with_name("mcp_fixture_server.py")


async def exercise(session, mock):
    await session.initialize()
    tools = await load_mcp_tools(session, allowed_tool_names=["read_memory"])
    assert [tool.name for tool in tools] == ["read_memory"]
    mock.push(tool_use_response(tool_use_id="read1", name="read_memory", input={"query": "hello"}, model="test"))
    mock.push(text_response("I found evidence", model="test"))
    events = [event async for event in query(prompt="Find work", options=LiteAgentOptions(model="test", tools=tools))]
    assert "evidence:hello" in json.dumps(events[1].content[0].content)
    assert not events[1].content[0].is_error
    with pytest.raises(RuntimeError, match="MCP tool failed"):
        await tools[0].execute({"query": "fail"})
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(tools[0].execute({"query": "slow"}), timeout=0.1)
    # Cancellation must leave the caller-owned session usable.
    assert "evidence:after" in json.dumps(await tools[0].execute({"query": "after"}))


async def test_real_stdio_mcp_session(mock_anthropic_messages):
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await exercise(session, mock_anthropic_messages)


@pytest.mark.parametrize("protocol", ["streamable-http", "sse"])
async def test_real_authenticated_remote_mcp_session(mock_anthropic_messages, protocol):
    from mcp.client import streamable_http as transport

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = await asyncio.create_subprocess_exec(
        sys.executable, str(SERVER), str(port), protocol, stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        for _ in range(200):
            if process.returncode is not None:
                raise AssertionError("MCP test server exited before startup")
            try:
                read, write = await asyncio.open_connection("127.0.0.1", port)
                write.close()
                await write.wait_closed()
                break
            except OSError:
                await asyncio.sleep(0.025)
        else:
            raise AssertionError("MCP test server did not start")
        url = f"http://127.0.0.1:{port}/mcp"
        # MCP 2.x uses httpx2; use the transport's matching HTTP client.
        if protocol == "sse":
            from mcp.client.sse import sse_client

            async with (
                sse_client(f"http://127.0.0.1:{port}/sse", headers={"Authorization": "Bearer synthetic"}) as (read, write),
                ClientSession(read, write) as session,
            ):
                await exercise(session, mock_anthropic_messages)
        elif hasattr(transport, "streamable_http_client"):
            httpx = getattr(transport, "httpx2", None) or transport.httpx
            async with (
                httpx.AsyncClient(headers={"Authorization": "Bearer synthetic"}) as http,
                transport.streamable_http_client(url, http_client=http) as streams,
                ClientSession(*streams[:2]) as session,
            ):
                await exercise(session, mock_anthropic_messages)
        else:
            async with (
                transport.streamablehttp_client(url, headers={"Authorization": "Bearer synthetic"}) as (read, write, _),
                ClientSession(read, write) as session,
            ):
                await exercise(session, mock_anthropic_messages)
    finally:
        process.terminate()
        await asyncio.wait_for(process.wait(), timeout=10)
