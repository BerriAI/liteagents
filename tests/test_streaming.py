import asyncio
import json
from contextlib import aclosing

import litellm
import pytest

from liteagents import AssistantMessage, LiteAgentClient, LiteAgentOptions, TextDelta, query
from liteagents._internal.streaming import stream_response

from .test_loop import EchoTool


def response_events(*, text="hello", tool=False):
    block = {"type": "tool_use", "id": "tool1", "name": "echo", "input": {}} if tool else {"type": "text", "text": ""}
    events = [
        {"type": "message_start", "message": {"model": "model", "usage": {"input_tokens": 12, "output_tokens": 0}}},
        {"type": "content_block_start", "index": 0, "content_block": block},
    ]
    parts = ['{"text":', '"hello"}'] if tool else [text[:2], text[2:]]
    for part in parts:
        delta = {"type": "input_json_delta", "partial_json": part} if tool else {"type": "text_delta", "text": part}
        events.append({"type": "content_block_delta", "index": 0, "delta": delta})
    events.extend([
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use" if tool else "end_turn"}, "usage": {"output_tokens": 8}},
        {"type": "message_stop"},
    ])
    return events


class Stream:
    def __init__(self, events, *, chunk_size=None):
        if chunk_size is None:
            self.events = iter(events)
        else:
            raw = "".join(f'event: {e["type"]}\r\ndata: {json.dumps(e, ensure_ascii=False)}\r\n\r\n' for e in events).encode()
            self.events = iter(raw[i:i + chunk_size] for i in range(0, len(raw), chunk_size))
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.events)
        except StopIteration:
            raise StopAsyncIteration from None

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize("chunk_size", [None, 1, 7, 1024, 100000])
async def test_stream_assembles_arbitrary_chunks_unicode_and_usage(chunk_size):
    stream = Stream(response_events(text="héllo 🦊"), chunk_size=chunk_size)
    events = [e async for e in stream_response(stream, model="fallback")]
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "héllo 🦊"
    assert events[-1]["content"] == [{"type": "text", "text": "héllo 🦊"}]
    assert events[-1]["usage"] == {"input_tokens": 12, "output_tokens": 8}
    assert events[-1]["stop_reason"] == "end_turn"
    assert stream.closed


async def test_stream_tool_roundtrip_executes_only_complete_arguments(monkeypatch):
    streams = [Stream(response_events(tool=True), chunk_size=7), Stream(response_events(text="done"))]
    calls = []

    async def request(**kwargs):
        calls.append(kwargs)
        return streams[len(calls) - 1]

    monkeypatch.setattr(litellm, "anthropic_messages", request)
    options = LiteAgentOptions(model="test", stream=True, tools=[EchoTool()])
    async with LiteAgentClient(options=options) as client:
        events = [e async for e in client.query("echo hello")]
        assert all(not isinstance(item, TextDelta) for item in client.history)
        assert events[-1].content[0].text == "done"
        assert calls[1]["messages"][-1]["content"][0]["content"] == "echoed: hello"
        assert all(call["stream"] for call in calls)
        assert all(stream.closed for stream in streams)


@pytest.mark.parametrize("wrapper", ["client", "query"])
async def test_early_close_releases_provider_stream(monkeypatch, wrapper):
    stream = Stream(response_events())

    async def request(**kwargs):
        return stream

    monkeypatch.setattr(litellm, "anthropic_messages", request)
    options = LiteAgentOptions(model="test", stream=True)
    if wrapper == "client":
        client = LiteAgentClient(options=options)
        events = client.query("hello")
    else:
        events = query(prompt="hello", options=options)
    async with aclosing(events):
        assert isinstance(await anext(events), TextDelta)
    assert stream.closed


async def test_cancel_during_provider_read_releases_stream(monkeypatch):
    entered = asyncio.Event()

    class WaitingStream(Stream):
        async def __anext__(self):
            entered.set()
            await asyncio.Event().wait()

    stream = WaitingStream([])

    async def request(**kwargs):
        return stream

    monkeypatch.setattr(litellm, "anthropic_messages", request)

    async def consume():
        return [e async for e in query(prompt="hello", options=LiteAgentOptions(model="test", stream=True))]

    task = asyncio.create_task(consume())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed


@pytest.mark.parametrize("events", [response_events()[:-1], [{"type": "error", "error": {"message": "SECRET"}}]])
async def test_incomplete_or_failed_stream_never_yields_final_response(events):
    stream = Stream(events)
    received = []
    with pytest.raises((ValueError, RuntimeError)) as error:
        async for event in stream_response(stream, model="test"):
            received.append(event)
    assert "SECRET" not in str(error.value)
    assert not any(isinstance(event, (dict, AssistantMessage)) for event in received)
    assert stream.closed


async def test_malformed_tool_json_does_not_execute(monkeypatch):
    events = response_events(tool=True)
    events[2]["delta"]["partial_json"] = "invalid"
    stream = Stream(events)

    async def request(**kwargs):
        return stream

    monkeypatch.setattr(litellm, "anthropic_messages", request)
    with pytest.raises(ValueError):
        _ = [e async for e in query(prompt="q", options=LiteAgentOptions(model="test", stream=True, tools=[EchoTool()]))]
    assert stream.closed
