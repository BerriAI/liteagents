"""Translate native harness requests with LiteLLM, using one configured model.

The harness still owns the agent loop. Only its provider wire protocol changes.
Completed native responses are recorded by NativeGateway before tool dispatch.
"""

from __future__ import annotations

import copy
import json
from typing import Any, cast

from ..errors import ConfigurationError
from .native_protocol import ResponseRecorder, managed_name
from .wire_response import encode_response

SETTINGS = {
    "api_base",
    "api_key",
    "temperature",
    "top_p",
    "max_tokens",
    "stop",
    "seed",
    "presence_penalty",
    "frequency_penalty",
    "reasoning_effort",
    "timeout",
}


def validate_settings(profile):
    unknown = profile.model_kwargs.keys() - SETTINGS
    if unknown:
        raise ConfigurationError(f"Unsupported shared model settings: {sorted(unknown)}")
    if profile.model.startswith("litellm_proxy/") and not profile.model_kwargs.get("api_base"):
        raise ConfigurationError("litellm_proxy models require model_kwargs.api_base")
    if "/" not in profile.model:
        raise ConfigurationError("Use provider/model or litellm_proxy/alias for shared execution")


def translate_request(path: str, body: dict) -> dict:
    from litellm.llms.anthropic.experimental_pass_through.adapters.transformation import (
        AnthropicAdapter,
    )
    from litellm.responses.litellm_completion_transformation.transformation import (
        LiteLLMCompletionResponsesConfig,
    )

    data = copy.deepcopy(body)
    if path == "messages":
        return dict(AnthropicAdapter().translate_completion_input_params(data) or {})
    if path == "responses":
        if data.get("previous_response_id"):
            raise ConfigurationError("Shared execution requires explicit conversation history")
        flattened: list[dict[str, Any]] = []
        for tool in data.get("tools", []):
            if tool.get("type") == "namespace":
                flattened.extend(
                    {**child, "name": tool["name"] + "." + child["name"]} for child in tool["tools"]
                )
            else:
                flattened.append(tool)
        data["tools"] = flattened
        for item in data.get("input", []):
            if (
                isinstance(item, dict)
                and item.get("type") == "function_call"
                and item.get("namespace")
            ):
                item["name"] = item.pop("namespace") + "." + item["name"]
        return LiteLLMCompletionResponsesConfig.transform_responses_api_request_to_chat_completion_request(
            model=data["model"],
            input=data.get("input", []),
            responses_api_request=cast(Any, data),
        )
    return data


def map_names(request: dict, names: set[str]) -> dict[str, str]:
    """The upstream model sees application names; the native loop gets its own names."""
    mapping = {}
    for tool in request.get("tools", []):
        definition = tool.get("function", tool)
        native = definition["name"]
        public = managed_name(native, names)
        if public is None:
            raise ConfigurationError("Provider requested an unmanaged tool definition")
        definition["name"] = public
        mapping[public] = native
    for message in request.get("messages", []):
        for call in message.get("tool_calls", []):
            function = call["function"]
            function["name"] = managed_name(function["name"], names) or function["name"]
    return mapping


def completion_arguments(profile, translated, model):
    arguments: dict[str, Any] = {"messages": translated.get("messages", [])}
    if translated.get("tools"):
        arguments["tools"] = translated["tools"]
    arguments.update(profile.model_kwargs)
    if model.startswith("litellm_proxy/"):
        model = "openai/" + model.split("/", 1)[1]
    return {"model": model, **arguments}


def stream_completed(stream, finished):
    # LiteLLM may synthesize a final "stop" chunk after an upstream EOF. That
    # chunk cannot authorize a tool call or a durable completed operation.
    if hasattr(stream, "received_finish_reason"):
        return finished and bool(
            stream.received_finish_reason or getattr(stream, "intermittent_finish_reason", None)
        )
    return finished


async def complete(profile, path, body, *, names, control, history=()):
    import litellm

    if path == "messages/count_tokens":
        # A local estimate is sufficient for the native context-window preflight.
        count = litellm.token_counter(model=profile.model, text=json.dumps(body))
        return {
            "body": json.dumps({"input_tokens": count}),
            "content_type": "application/json",
            "calls": [],
        }
    translated = translate_request(path, body)
    mapping = map_names(translated, names)
    messages = translated.get("messages", [])
    if history:
        from .conversation import chat_history

        index = next(
            (i for i, m in enumerate(messages) if m["role"] not in ("system", "developer")),
            len(messages),
        )
        messages[index:index] = chat_history(history)
    # Native runtime defaults (thinking budget, temperature, output budget) must
    # not override the application's model configuration when switching harness.
    arguments = completion_arguments(profile, translated, body["model"])
    response = await litellm.acompletion(
        **arguments,
        stream=True,
        stream_options={"include_usage": True},
        num_retries=0,
        max_retries=0,
        drop_params=False,
    )
    chunks = []
    size = 0
    limit = profile.temporal.max_payload_bytes if profile.temporal else 8_000_000
    finished = False
    try:
        async for chunk in response:
            size += len(chunk.model_dump_json().encode())
            if size > limit:
                raise ConfigurationError("Provider response exceeds max_payload_bytes")
            chunks.append(chunk)
            for choice in chunk.choices:
                finished = finished or bool(choice.finish_reason)
                text = choice.delta.content
                if text and profile.features.streaming:
                    await control.emit("text_delta", text=text, model=body["model"])
    finally:
        await response.aclose()
    if not stream_completed(response, finished):
        raise ConnectionError("Provider stream ended before its completion event")
    assembled = litellm.stream_chunk_builder(chunks, messages=messages)
    if assembled is None:
        raise ConnectionError("Provider returned no completed response")
    for choice in assembled.choices:
        for call in choice.message.tool_calls or []:
            name = call.function.name
            if name not in mapping:
                raise ConfigurationError("Provider requested a tool outside the selected tools")
            call.function.name = mapping[name]
    wire, native = encode_response(path, assembled, body)
    recorder = ResponseRecorder()
    recorder.json_response(native)
    return {
        "body": wire,
        "content_type": "text/event-stream" if body.get("stream") else "application/json",
        "calls": recorder.results(),
    }
