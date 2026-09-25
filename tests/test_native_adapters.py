"""Protocol fault injection; live tests separately exercise the actual executables."""

import asyncio
from contextlib import aclosing
from types import SimpleNamespace

import httpx
import pytest

from liteagents import (
    AssistantMessage,
    HarnessError,
    ProfileOptions,
    TextDelta,
    ToolUseBlock,
    UserMessage,
)


def notification(method, **params):
    return SimpleNamespace(method=method, payload=SimpleNamespace(params=params))


@pytest.fixture
def codex_adapter(tmp_path):
    pytest.importorskip("openai_codex")
    from liteagents.harnesses.codex import CodexAdapter

    return CodexAdapter(
        ProfileOptions(harness="codex", model="openai/test", features={"streaming": True}),
        cwd=tmp_path,
        tools=[],
        session_id="test",
    )


class Turn:
    def __init__(self, events):
        self.events = events
        self.interrupted = False

    async def stream(self):
        for event in self.events:
            yield event

    async def interrupt(self):
        self.interrupted = True
        raise RuntimeError("cleanup must not replace provider error")


def connect_turn(adapter, events):
    turn = Turn(events)

    async def start(*args, **kwargs):
        return turn

    adapter.thread = SimpleNamespace(turn=start)
    return turn


async def test_codex_correlates_tool_and_normalizes_usage(codex_adapter):
    item = {
        "id": "tool1",
        "type": "commandExecution",
        "command": "cat input.txt",
        "status": "completed",
        "aggregatedOutput": "CORAL",
    }
    turn = connect_turn(
        codex_adapter,
        [
            notification("item/started", item=item),
            notification("item/completed", item=item),
            notification("item/agentMessage/delta", delta="answer"),
            notification(
                "item/completed", item={"id": "a", "type": "agentMessage", "text": "answer"}
            ),
            notification("thread/tokenUsage/updated", tokenUsage={"last": {"inputTokens": 12}}),
            notification("turn/completed", turn={"status": "completed"}),
        ],
    )
    events = [e async for e in codex_adapter.query("read", run_id="r")]
    assert isinstance(events[0].content[0], ToolUseBlock)
    assert isinstance(events[1], UserMessage)
    assert events[0].content[0].id == events[1].content[0].tool_use_id
    assert isinstance(events[2], TextDelta)
    assert events[-1].usage == {"inputTokens": 12}
    assert not turn.interrupted


@pytest.mark.parametrize(
    "events,match",
    [
        (
            [notification("turn/completed", turn={"status": "failed", "error": "upstream 429"})],
            "upstream 429",
        ),
        ([], "without a completed"),
        ([notification("turn/completed", turn={"status": "completed"})], "without an assistant"),
    ],
)
async def test_codex_errors_are_not_masked_by_interrupt(codex_adapter, events, match):
    connect_turn(codex_adapter, events)
    with pytest.raises(HarnessError, match=match):
        _ = [e async for e in codex_adapter.query("read", run_id="r")]


async def test_codex_closing_generator_interrupts_turn(codex_adapter):
    turn = connect_turn(codex_adapter, [notification("item/agentMessage/delta", delta="partial")])
    async with aclosing(codex_adapter.query("read", run_id="r")) as stream:
        await anext(stream)
    assert turn.interrupted


@pytest.mark.parametrize("failure", [False, True])
async def test_claude_native_messages_and_terminal_error(tmp_path, failure):
    sdk = pytest.importorskip("claude_agent_sdk")
    from liteagents.harnesses.claude_sdk import ClaudeAdapter

    adapter = ClaudeAdapter(
        ProfileOptions(harness="claude-sdk", model="anthropic/test"),
        cwd=tmp_path,
        tools=[],
        session_id="test",
    )
    messages = [
        sdk.SystemMessage("init", {"session_id": "native"}),
        sdk.AssistantMessage([sdk.ToolUseBlock("t", "Read", {"file_path": "input.txt"})], "test"),
        sdk.UserMessage([sdk.ToolResultBlock("t", "CORAL")]),
        sdk.AssistantMessage([sdk.TextBlock("answer")], "test"),
        sdk.ResultMessage(
            "error_max_turns" if failure else "success",
            1,
            1,
            failure,
            2,
            "native",
            result="model limit" if failure else "answer",
            usage={"input_tokens": 12},
        ),
    ]

    class Client:
        async def query(self, prompt):
            pass

        async def receive_response(self):
            for m in messages:
                yield m

        async def interrupt(self):
            raise AssertionError("must not interrupt terminal run")

    adapter.client = Client()
    adapter._connected = True
    if failure:
        with pytest.raises(HarnessError, match="model limit"):
            _ = [e async for e in adapter.query("read", run_id="r")]
    else:
        events = [e async for e in adapter.query("read", run_id="r")]
        assert adapter.native_session_id == "native"
        assert events[0].content[0].id == events[1].content[0].tool_use_id
        assert events[-1].usage == {"input_tokens": 12}


@pytest.mark.parametrize("mode", ["success", "error", "empty", "invalid"])
async def test_opencode_http_contract_and_failed_cleanup(tmp_path, mode):
    from liteagents.harnesses.opencode import OpenCodeAdapter

    adapter = OpenCodeAdapter(
        ProfileOptions(harness="opencode-v2", model="openai/test", tools=["read_file"]),
        cwd=tmp_path,
        tools=[],
        session_id="test",
    )
    adapter.native_session_id = "session"
    adapter.model_spec = {"providerID": "openai", "modelID": "test"}
    adapter.agent_name = "liteagents"
    submitted = False
    paths = []

    def handler(request):
        nonlocal submitted
        paths.append(request.url.path)
        if request.url.path == "/experimental/tool/ids":
            return httpx.Response(200, json=["read", "edit"])
        if request.method == "POST":
            if request.url.path.endswith("abort"):
                return httpx.Response(503)
            import json

            assert json.loads(request.content)["tools"] == {"read": True, "edit": False}
            submitted = True
            return httpx.Response(200, json={})
        if not submitted:
            return httpx.Response(200, json=[])
        if mode == "invalid":
            return httpx.Response(200, text="not json")
        if mode == "empty":
            return httpx.Response(200, json=[])
        info = {"id": "a", "role": "assistant", "finish": "stop"}
        if mode == "error":
            info["error"] = {"message": "upstream unavailable"}
        return httpx.Response(
            200,
            json=[
                {
                    "info": info,
                    "parts": [
                        {
                            "type": "tool",
                            "callID": "t",
                            "tool": "read",
                            "state": {
                                "status": "completed",
                                "input": {"path": "input.txt"},
                                "output": "CORAL",
                            },
                        },
                        {"type": "text", "text": "answer"},
                    ],
                }
            ],
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    ) as http:
        adapter.http = http
        if mode == "success":
            events = [e async for e in adapter.query("read", run_id="r")]
            assert events[0].content[0].id == events[1].content[0].tool_use_id
            assert isinstance(events[-1], AssistantMessage)
            assert not any(p.endswith("abort") for p in paths)
        else:
            with pytest.raises(HarnessError):
                _ = [e async for e in adapter.query("read", run_id="r")]
            assert paths[-1].endswith("abort")


async def test_opencode_sse_disconnect_and_multiline_data():
    from liteagents.harnesses.opencode import read_sse

    class Events(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"type":\ndata: "ping"}\n\n'

    queue = asyncio.Queue()
    async with aclosing(httpx.Response(200, stream=Events())) as response:
        await read_sse(response, queue)
    assert await queue.get() == {"type": "ping"}
    assert "_error" in await queue.get()


@pytest.mark.integration
async def test_owned_opencode_servers_start_concurrently_and_reopen_sessions(tmp_path):
    import shutil

    if not shutil.which("opencode"):
        pytest.skip("Install OpenCode 1.18.29 for real server lifecycle coverage")
    from liteagents.harnesses.opencode import OpenCodeAdapter

    adapters = []
    for name in ("opencode-v1", "opencode-v2"):
        cwd = tmp_path / name
        cwd.mkdir()
        adapters.append(
            OpenCodeAdapter(
                ProfileOptions(harness=name, model="openai/test"),
                cwd=cwd,
                tools=[],
                session_id="unused",
            )
        )
    try:
        outcomes = await asyncio.gather(*(a.open() for a in adapters), return_exceptions=True)
        assert all(result is None for result in outcomes), outcomes
        assert adapters[0].http.base_url != adapters[1].http.base_url
        from liteagents import ConfigurationError

        duplicate = OpenCodeAdapter(
            adapters[0].profile, cwd=adapters[0].cwd, tools=[], session_id="duplicate"
        )
        with pytest.raises(ConfigurationError, match="owns this state_dir"):
            await duplicate.open()
        await duplicate.close()
        original = adapters[0].native_session_id
        await adapters[0].close()
        resumed = OpenCodeAdapter(
            adapters[0].profile,
            cwd=adapters[0].cwd,
            tools=[],
            session_id=original,
            resume_session=True,
        )
        adapters.append(resumed)
        await resumed.open()
        assert resumed.native_session_id == original
    finally:
        await asyncio.gather(*(a.close() for a in adapters))
    assert all(a.process.returncode is not None for a in adapters if a.process)
