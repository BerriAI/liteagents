"""An internal Chat Completions endpoint backed by the LiteLLM provider registry.

Python harnesses use their native OpenAI model integrations against this local
endpoint. The application keeps its original provider/model and credentials.
"""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from aiohttp import web

from ..errors import ConfigurationError
from .control import retryable
from .model_bridge import completion_arguments, stream_completed, validate_settings


class ModelEndpoint:
    def __init__(self, profile):
        validate_settings(profile)
        self.profile = profile
        self.token = uuid4().hex
        self.handlers: set[asyncio.Task] = set()

    async def __aenter__(self):
        app = web.Application(client_max_size=50_000_000)
        app.router.add_post("/" + self.token + "/v1/chat/completions", self.respond)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", 0).start()
        self.url = f"http://127.0.0.1:{self.runner.addresses[0][1]}/{self.token}/v1"
        return self

    async def __aexit__(self, *args):
        pending = list(self.handlers)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await self.runner.cleanup()

    async def respond(self, request):
        import litellm

        task = asyncio.current_task()
        self.handlers.add(task)
        stream = None
        response = None
        try:
            body = await request.json()
            arguments = completion_arguments(self.profile, body, self.profile.model)
            stream = await litellm.acompletion(
                **arguments,
                stream=True,
                stream_options={"include_usage": True},
                num_retries=0,
                max_retries=0,
                drop_params=False,
            )
            chunks = []
            finished = False
            size = 0
            limit = self.profile.temporal.max_payload_bytes if self.profile.temporal else 8_000_000
            if body.get("stream"):
                response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
                await response.prepare(request)
            async for chunk in stream:
                wire = chunk.model_dump_json(exclude_none=True)
                size += len(wire.encode())
                if size > limit:
                    raise ConfigurationError("Provider response exceeds max_payload_bytes")
                finished = finished or any(choice.finish_reason for choice in chunk.choices)
                if response is not None:
                    await response.write(("data: " + wire + "\n\n").encode())
                else:
                    chunks.append(chunk)
            if not stream_completed(stream, finished):
                raise ConnectionError("Provider stream ended before its completion event")
            if response is not None:
                await response.write(b"data: [DONE]\n\n")
                await response.write_eof()
                return response
            assembled = litellm.stream_chunk_builder(chunks, messages=arguments["messages"])
            return web.json_response(assembled.model_dump(mode="json", exclude_none=True))
        except Exception as exc:  # noqa: BLE001 - errors cross the private HTTP boundary
            if response is not None:
                # An incomplete stream must fail instead of becoming a completed
                # operation. Include an OpenAI error frame so native SDKs raise.
                wire = {
                    "error": {
                        "message": f"Provider stream failed: {type(exc).__name__}",
                        "code": "liteagents_transient_stream"
                        if retryable(exc)
                        else "liteagents_permanent_stream",
                    }
                }
                await response.write(("data: " + json.dumps(wire) + "\n\n").encode())
                await response.write_eof()
                return response
            status = (
                400
                if isinstance(exc, (ConfigurationError, ValueError))
                else getattr(exc, "status_code", 500)
            )
            return web.json_response(
                {
                    "error": {
                        "message": f"LiteLLM provider failed: {type(exc).__name__}",
                        "type": "provider_error",
                    }
                },
                status=status if isinstance(status, int) and 400 <= status < 600 else 500,
            )
        finally:
            if stream is not None:
                await stream.aclose()
            self.handlers.discard(task)


async def translated_profile(profile, stack):
    endpoint = await stack.enter_async_context(ModelEndpoint(profile))
    return profile.model_copy(
        update={
            "model": "openai/" + profile.model,
            "model_kwargs": {"api_base": endpoint.url, "api_key": "liteagents-local"},
        }
    )
