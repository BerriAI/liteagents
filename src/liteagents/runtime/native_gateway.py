"""Private local provider/MCP gateway for controlled native-process execution.

Complete model responses are committed before tool frames reach the child. Live
text goes directly to the run event stream while responses are being recorded.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any
from uuid import uuid4

import httpx
from aiohttp import web

from ..errors import ConfigurationError
from ..tools import Tool
from .control import CURRENT, SCOPE, RunControl, retryable
from .native_protocol import (
    ResponseRecorder,
    canonical,
    managed_name,
    select_definitions,
    split_frames,
    sse_payload,
)


class NativeGateway:
    def __init__(self, profile, tools: list[Tool]):
        self.profile = profile
        self.tools = {t.name: t for t in tools}
        self.control: RunControl | None = None
        self.sequence: dict[str, int] = defaultdict(int)
        self.pending: list[dict[str, Any]] = []
        self.used: set[str] = set()
        self.error: Exception | None = None
        self.token = uuid4().hex
        self.http = httpx.AsyncClient(timeout=300)
        self.lock = asyncio.Lock()
        self.handlers: set[asyncio.Task] = set()

    async def open(self):
        @web.middleware
        async def track(request, handler):
            task = asyncio.current_task()
            self.handlers.add(task)
            try:
                return await handler(request)
            finally:
                self.handlers.discard(task)

        app = web.Application(client_max_size=50_000_000, middlewares=[track])
        app.router.add_route("*", "/{token}/mcp", self.mcp)
        app.router.add_post("/{token}/{path:.*}", self.provider)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = self.runner.addresses[0][1]
        self.url = f"http://127.0.0.1:{port}/{self.token}"

    async def close(self):
        pending = list(self.handlers)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if hasattr(self, "runner"):
            await self.runner.cleanup()
        await self.http.aclose()

    def bind(self, control):
        self.control = control
        self.scope = SCOPE.get()
        self.sequence.clear()
        self.pending.clear()
        self.used.clear()
        self.error = None

    async def mcp(self, request):
        if request.match_info["token"] != self.token:
            raise web.HTTPNotFound()
        if request.method != "POST":
            raise web.HTTPMethodNotAllowed(request.method, ["POST"])
        body = await request.json()
        if "id" not in body:
            return web.Response(status=202)
        method, params = body["method"], body.get("params", {})
        if method == "initialize":
            result = {
                "protocolVersion": params["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "liteagents", "version": "0.2.0"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {"name": t.name, "description": t.description, "inputSchema": t.input_schema}
                    for t in self.tools.values()
                ]
            }
        elif method == "ping":
            result = {}
        elif method == "tools/call":
            try:
                result = await self.invoke_tool(params["name"], params.get("arguments", {}))
            except Exception as exc:  # noqa: BLE001 - surface through the run and protocol
                self.error = exc
                result = {
                    "isError": True,
                    "content": [
                        {"type": "text", "text": f"LiteAgents tool failed: {type(exc).__name__}"}
                    ],
                }
        else:
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "error": {"code": -32601, "message": "Method not found"},
                }
            )
        return web.json_response({"jsonrpc": "2.0", "id": body["id"], "result": result})

    async def invoke_tool(self, name, arguments):
        if self.error:
            raise self.error
        if self.control is None:
            raise ConfigurationError("No active run")
        async with self.lock:
            match = next(
                (
                    call
                    for call in self.pending
                    if call["operation_key"] not in self.used
                    and managed_name(call["name"], set(self.tools)) == name
                    and call["input"] == arguments
                ),
                None,
            )
            if match is None:
                raise ConfigurationError("Tool invocation has no committed model operation")
            self.used.add(match["operation_key"])

        async def invoke():
            return await self.tools[name].execute(arguments)

        token, scope = CURRENT.set(self.control), SCOPE.set(self.scope)
        try:
            output = await self.control.call(
                "tool", match["operation_key"], {"name": name, "input": arguments}, invoke
            )
        finally:
            CURRENT.reset(token)
            SCOPE.reset(scope)
        if isinstance(output, str):
            content = [{"type": "text", "text": output}]
        elif isinstance(output, list):
            content = []
            for item in output:
                if item.get("type") == "image" and "source" in item:
                    source = item["source"]
                    content.append(
                        {"type": "image", "data": source["data"], "mimeType": source["media_type"]}
                    )
                else:
                    content.append(item)
        else:
            content = [{"type": "text", "text": json.dumps(output)}]
        return {"content": content}

    async def provider(self, request):
        if request.match_info["token"] != self.token:
            raise web.HTTPNotFound()
        if self.control is None:
            return web.json_response({"error": {"message": "No active LiteAgents run"}}, status=400)
        try:
            scope = SCOPE.set(self.scope)
            try:
                return await self.model_request(request)
            finally:
                SCOPE.reset(scope)
        except Exception as exc:  # noqa: BLE001 - no native retries beyond the recorded budget
            self.error = exc
            return web.json_response(
                {
                    "error": {
                        "type": "invalid_request_error",
                        "message": f"LiteAgents model operation failed: {type(exc).__name__}",
                    }
                },
                status=400,
            )

    async def model_request(self, request):
        if self.error:
            raise self.error
        assert self.control is not None
        await self.control.check()
        body = await request.json()
        path = request.match_info["path"].removeprefix("v1/")
        if path not in ("messages", "responses", "chat/completions", "messages/count_tokens"):
            raise ConfigurationError("Unsupported provider operation: " + path)
        definitions = body.get("tools", [])
        selected = select_definitions(definitions, set(self.tools))
        # Keep the native loop, but remove every tool that bypasses the journal.
        if definitions:
            body["tools"] = selected
        lane = path + (":agent" if definitions else ":aux")
        self.sequence[lane] += 1
        if definitions and self.sequence[lane] > (self.profile.max_turns or 20):
            raise ConfigurationError("Native model-call limit reached")
        key = f"native:{lane}:{self.sequence[lane]}"
        original = body.get("model", self.profile.model)
        names = [
            original,
            *(self.profile.recovery.model_fallbacks if self.profile.recovery else []),
        ]
        headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() in ("anthropic-version", "anthropic-beta", "content-type")
        }
        api_key = self.profile.model_kwargs.get("api_key")
        if api_key:
            headers["authorization"] = "Bearer " + api_key
            headers["x-api-key"] = api_key
        base = self.profile.model_kwargs["api_base"].rstrip("/").removesuffix("/v1")
        for index, name in enumerate(names):
            native_name = (
                name.split("/", 1)[1]
                if name.startswith(("litellm_proxy/", "openai/", "anthropic/"))
                else name
            )
            data = {**body, "model": native_name}
            arguments = canonical(data)

            async def invoke(data=data):
                return await self.forward(
                    base + "/v1/" + path, headers, data, publish=bool(definitions)
                )

            try:
                saved = await self.control.call("model", key + ":" + str(index), arguments, invoke)
                break
            except Exception as exc:
                if index == len(names) - 1 or not retryable(exc):
                    raise
                await self.control.emit("model_fallback", previous=name, model=names[index + 1])
        self.pending.extend(
            {**call, "operation_key": key + ":" + call["id"]} for call in saved["calls"]
        )
        return web.Response(body=saved["body"].encode(), content_type=saved["content_type"])

    async def forward(self, url, headers, body, *, publish=True):
        assert self.control is not None
        recorder = ResponseRecorder()
        chunks = []
        buffered = ""
        size = 0
        async with self.http.stream("POST", url, headers=headers, json=body) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "application/json").split(";")[0]
            async for chunk in response.aiter_text():
                chunks.append(chunk)
                size += len(chunk.encode())
                if size > (
                    self.profile.temporal.max_payload_bytes if self.profile.temporal else 8_000_000
                ):
                    raise ConfigurationError("Provider response exceeds max_payload_bytes")
                if content_type == "text/event-stream":
                    frames, buffered = split_frames(buffered + chunk)
                    for frame in frames:
                        event = sse_payload(frame)
                        if event is not None:
                            text = recorder.read(event)
                            if text and publish and self.profile.features.streaming:
                                await self.control.emit(
                                    "text_delta", text=text, model=body["model"]
                                )
            wire = "".join(chunks)
            if content_type != "text/event-stream":
                recorder.json_response(json.loads(wire))
            elif not recorder.complete:
                raise ConnectionError("Provider stream ended before its completion event")
        calls = recorder.results()
        for call in calls:
            if not managed_name(call["name"], set(self.tools)):
                raise ConfigurationError("Provider requested a tool outside managed MCP")
        return {"body": wire, "content_type": content_type, "calls": calls}
