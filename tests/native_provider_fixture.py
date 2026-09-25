"""A deterministic provider fixture driven by real native CLI requests."""

import json
from contextlib import asynccontextmanager
from uuid import uuid4

from aiohttp import web

from liteagents.runtime.native_protocol import tool_name


class Provider:
    def __init__(self):
        self.requests = []
        self.responses = []
        self.fail_chat = False
        self.fail_after_tool = False
        self.fail_models = set()
        self.forced_tool = None

    async def reply(self, request):
        data = await request.json()
        self.requests.append(data)
        if request.path.endswith("count_tokens"):
            return web.json_response({"input_tokens": 100})
        if data.get("model") in self.fail_models:
            return web.json_response(
                {"error": {"message": "Synthetic model failure"}}, status=503
            )
        definitions = data.get("tools", [])
        names = [
            name
            for t in definitions
            for name in (
                [t["name"] + "." + tool_name(child) for child in t["tools"]]
                if t.get("type") == "namespace"
                else [tool_name(t)]
            )
        ]
        history = json.dumps(data.get("messages", data.get("input", [])))
        if request.path.endswith("chat/completions") and (
            self.fail_chat or self.fail_after_tool and "USD 12" in history
        ):
            return web.json_response(
                {"error": {"message": "Synthetic transient failure"}}, status=503
            )
        wanted = "slow" if "USD 12" in history else "lookup"
        name = next((n for n in names if n.endswith(wanted)), None)
        if "Validated USD 12" in history:
            name = None
        delegate = next((n for n in names if n.endswith("delegate_auditor")), None)
        arguments = {}
        if delegate and "Validated USD 12" not in history:
            name = delegate
            wanted = "delegate"
            arguments = {"prompt": "Read and validate the order"}
        call_id = "call_" + wanted
        if self.forced_tool:
            name = self.forced_tool
        text = "Validated USD 12"
        model = data.get("model", "scripted")
        identifier = "msg_" + uuid4().hex
        frames = []
        if request.path.endswith("/messages"):
            block = (
                {"type": "tool_use", "id": call_id, "name": name, "input": arguments}
                if name
                else {"type": "text", "text": text}
            )
            response = {
                "id": identifier,
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [block],
                "stop_reason": "tool_use" if name else "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 100, "output_tokens": 20},
            }
            frames = [
                {
                    "type": "message_start",
                    "message": {**response, "content": [], "stop_reason": None},
                },
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": block if name else {"type": "text", "text": ""},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": json.dumps(arguments)}
                    if name
                    else {"type": "text_delta", "text": text},
                },
                {"type": "content_block_stop", "index": 0},
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": response["stop_reason"], "stop_sequence": None},
                    "usage": {"output_tokens": 20},
                },
                {"type": "message_stop"},
            ]
        elif request.path.endswith("/responses"):
            item = (
                {
                    "type": "function_call",
                    "id": "fc_" + wanted,
                    "call_id": call_id,
                    "name": name,
                    "arguments": json.dumps(arguments),
                    "status": "completed",
                }
                if name
                else {
                    "type": "message",
                    "id": identifier,
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text, "annotations": []}],
                }
            )
            if name and name.startswith("mcp__liteagents."):
                item["namespace"], item["name"] = name.split(".", 1)
            response = {
                "id": "resp_" + uuid4().hex,
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": model,
                "output": [item],
                "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
            }
            frames = [
                {
                    "type": "response.created",
                    "response": {**response, "status": "in_progress", "output": []},
                },
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {**item, "status": "in_progress"},
                },
                {"type": "response.output_item.done", "output_index": 0, "item": item},
                {"type": "response.completed", "response": response},
            ]
        else:
            message = (
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                    ],
                }
                if name
                else {"role": "assistant", "content": text}
            )
            response = {
                "id": identifier,
                "object": "chat.completion",
                "created": 1,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": "tool_calls" if name else "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            }
            delta = (
                {"tool_calls": [{"index": 0, **message["tool_calls"][0]}]}
                if name
                else {"content": text}
            )
            frames = [
                {
                    "id": identifier,
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": model,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                },
                {
                    "id": identifier,
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {},
                            "finish_reason": response["choices"][0]["finish_reason"],
                        }
                    ],
                    "usage": response["usage"],
                },
            ]
        self.responses.append(response)
        if data.get("stream"):
            chunks = []
            for index, event in enumerate(frames):
                if request.path.endswith("/responses"):
                    event["sequence_number"] = index
                prefix = "event: " + event["type"] + "\n" if "type" in event else ""
                chunks.append(prefix + "data: " + json.dumps(event) + "\n\n")
            if request.path.endswith("completions"):
                chunks.append("data: [DONE]\n\n")
            return web.Response(text="".join(chunks), content_type="text/event-stream")
        return web.json_response(response)

    @asynccontextmanager
    async def running(self):
        app = web.Application()
        app.router.add_post("/{path:.*}", self.reply)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        self.url = f"http://127.0.0.1:{runner.addresses[0][1]}/v1"
        try:
            yield self
        finally:
            await runner.cleanup()
