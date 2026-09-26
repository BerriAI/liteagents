"""Frame a completed LiteLLM response for the native harness's protocol."""

from __future__ import annotations

import json


def encode_response(path, response, request):
    if path == "messages":
        from litellm.llms.anthropic.experimental_pass_through.adapters.transformation import (
            AnthropicAdapter,
        )

        native = dict(AnthropicAdapter().translate_completion_output_params(response) or {})
        events = anthropic_events(native)
    elif path == "responses":
        from litellm.responses.litellm_completion_transformation.transformation import (
            LiteLLMCompletionResponsesConfig,
        )

        native = LiteLLMCompletionResponsesConfig.transform_chat_completion_response_to_responses_api_response(
            request_input=request.get("input", []),
            responses_api_request=request,
            chat_completion_response=response,
        ).model_dump(mode="json", exclude_none=True)
        for item in native["output"]:
            if item.get("type") == "function_call" and item["name"].startswith("mcp__liteagents."):
                item["namespace"], item["name"] = item["name"].split(".", 1)
        events = responses_events(native)
    else:
        native = response.model_dump(mode="json", exclude_none=True)
        events = chat_events(native)
    if not request.get("stream"):
        return json.dumps(native), native
    chunks = []
    for i, event in enumerate(events):
        if path == "responses":
            event["sequence_number"] = i
        prefix = "event: " + event["type"] + "\n" if "type" in event else ""
        chunks.append(prefix + "data: " + json.dumps(event) + "\n\n")
    if path == "chat/completions":
        chunks.append("data: [DONE]\n\n")
    return "".join(chunks), native


def anthropic_events(response):
    yield {"type": "message_start", "message": {**response, "content": [], "stop_reason": None}}
    for i, block in enumerate(response["content"]):
        tool = block["type"] == "tool_use"
        if not tool and block["type"] != "text":
            # Preserve completed thinking/signature blocks without inventing deltas.
            yield {"type": "content_block_start", "index": i, "content_block": block}
        else:
            yield {
                "type": "content_block_start",
                "index": i,
                "content_block": {**block, "input": {}} if tool else {"type": "text", "text": ""},
            }
            yield {
                "type": "content_block_delta",
                "index": i,
                "delta": {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
                if tool
                else {"type": "text_delta", "text": block["text"]},
            }
        yield {"type": "content_block_stop", "index": i}
    yield {
        "type": "message_delta",
        "delta": {"stop_reason": response["stop_reason"], "stop_sequence": None},
        "usage": response.get("usage", {}),
    }
    yield {"type": "message_stop"}


def responses_events(response):
    yield {
        "type": "response.created",
        "response": {**response, "status": "in_progress", "output": []},
    }
    for i, item in enumerate(response["output"]):
        yield {
            "type": "response.output_item.added",
            "output_index": i,
            "item": {**item, "status": "in_progress"},
        }
        yield {"type": "response.output_item.done", "output_index": i, "item": item}
    yield {"type": "response.completed", "response": response}


def chat_events(response):
    base = {k: response[k] for k in ("id", "created", "model")}
    for choice in response["choices"]:
        delta = dict(choice["message"])
        if delta.get("tool_calls"):
            delta["tool_calls"] = [
                {"index": i, **call} for i, call in enumerate(delta["tool_calls"])
            ]
        yield {
            **base,
            "object": "chat.completion.chunk",
            "choices": [{"index": choice["index"], "delta": delta, "finish_reason": None}],
        }
        yield {
            **base,
            "object": "chat.completion.chunk",
            "choices": [
                {"index": choice["index"], "delta": {}, "finish_reason": choice["finish_reason"]}
            ],
            "usage": response.get("usage", {}),
        }
