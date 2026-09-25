import asyncio
import socket
import sys
from contextlib import AsyncExitStack

import pytest

from liteagents import LiteAgentClient, LiteAgentOptions, ProfileOptions
from liteagents.runtime.tooling import load_servers
from tests.test_mcp_transport import SERVER


@pytest.mark.integration
@pytest.mark.parametrize("transport", ["stdio", "http", "sse"])
async def test_profile_manages_real_mcp_connection(transport):
    pytest.importorskip("mcp")
    process = None
    config = {"command": sys.executable, "args": [str(SERVER)], "allowed_tools": ["read_memory"]}
    if transport != "stdio":
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(SERVER),
            str(port),
            "sse" if transport == "sse" else "streamable-http",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        async with asyncio.timeout(10):
            while True:
                try:
                    _, writer = await asyncio.open_connection("127.0.0.1", port)
                    writer.close()
                    await writer.wait_closed()
                    break
                except OSError:
                    await asyncio.sleep(0.05)
        config = {
            "url": f"http://127.0.0.1:{port}/" + ("sse" if transport == "sse" else "mcp"),
            "transport": transport,
            "headers": {"Authorization": "Bearer synthetic"},
            "allowed_tools": ["read_memory"],
        }
    try:
        profile = ProfileOptions(
            harness="deepagents", model="scripted/test", mcp_servers={"evidence": config}
        )
        async with AsyncExitStack() as stack:
            tools = await load_servers(profile, stack)
            assert [t.name for t in tools] == ["evidence_read_memory"]
            assert "evidence:hello" in str(await tools[0].execute({"query": "hello"}))
    finally:
        if process is not None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                process.kill()
                await process.wait()


@pytest.mark.integration
@pytest.mark.parametrize("harness", ["deepagents", "pydantic-ai"])
async def test_native_python_loop_executes_profile_mcp(harness, tmp_path):
    pytest.importorskip("mcp")
    if harness == "deepagents":
        pytest.importorskip("deepagents")
        from langchain_core.language_models.chat_models import BaseChatModel
        from langchain_core.messages import AIMessage, ToolMessage
        from langchain_core.outputs import ChatGeneration, ChatResult

        class Model(BaseChatModel):
            @property
            def _llm_type(self):
                return "mcp-test"

            def bind_tools(self, tools, **kwargs):
                assert [t.name for t in tools] == ["evidence_read_memory"]
                return self

            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                result = next((m for m in messages if isinstance(m, ToolMessage)), None)
                message = (
                    AIMessage(content=str(result.content))
                    if result
                    else AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "m1",
                                "name": "evidence_read_memory",
                                "args": {"query": "verified"},
                            }
                        ],
                    )
                )
                return ChatResult(generations=[ChatGeneration(message=message)])

        model = Model()
    else:
        pytest.importorskip("pydantic_ai")
        from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
        from pydantic_ai.models.function import FunctionModel

        def respond(messages, info):
            result = next(
                (p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)), None
            )
            return ModelResponse(
                parts=[TextPart(str(result.content))]
                if result
                else [ToolCallPart("evidence_read_memory", {"query": "verified"}, "m1")]
            )

        model = FunctionModel(respond)
    profile = ProfileOptions(
        harness=harness,
        model="scripted/test",
        tools=["evidence_read_memory"],
        mcp_servers={
            "evidence": {
                "command": sys.executable,
                "args": [str(SERVER)],
                "allowed_tools": ["read_memory"],
            }
        },
        harness_options={"model_instance": model},
    )
    async with LiteAgentClient(options=LiteAgentOptions(profile=profile, cwd=tmp_path)) as client:
        _ = [e async for e in client.query("Find evidence", run_id="mcp")]
        assert "evidence:verified" in (await (await client.get_run("mcp")).result()).text
