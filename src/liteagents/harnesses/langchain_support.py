from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any

import httpx
from langchain_core.tools import StructuredTool

from ..errors import ConfigurationError
from ..profiles import ProfileOptions
from ..tools import Tool
from ..types import AssistantMessage, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage


def text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(
        part.get("text", "")
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    )


def convert_message(message: Any, model: str) -> AssistantMessage | UserMessage | None:
    if message.type == "ai":
        blocks: list[Any] = []
        text = text_content(message.content)
        if text:
            blocks.append(TextBlock(text))
        blocks.extend(
            ToolUseBlock(call["id"], call["name"], call["args"]) for call in message.tool_calls
        )
        if message.invalid_tool_calls:
            raise ConfigurationError("Model returned malformed tool-call arguments")
        if blocks:
            return AssistantMessage(
                blocks,
                model,
                "tool_use" if message.tool_calls else "end_turn",
                message.usage_metadata or None,
            )
    if message.type == "tool":
        return UserMessage(
            [
                ToolResultBlock(
                    message.tool_call_id,
                    message.content,
                    getattr(message, "status", None) == "error",
                )
            ]
        )
    return None


def wrap_tool(tool: Tool) -> StructuredTool:
    async def execute(**kwargs: Any) -> Any:
        return await tool.execute(kwargs)

    return StructuredTool(
        name=tool.name,
        description=tool.description,
        args_schema=tool.input_schema,
        coroutine=execute,
    )


async def build_model(profile: ProfileOptions, stack: AsyncExitStack) -> Any:
    if "model_instance" in profile.harness_options:
        if profile.model_kwargs:
            raise ConfigurationError("Configure model_kwargs on the supplied model_instance")
        return profile.harness_options["model_instance"]
    provider, separator, name = profile.model.partition("/")
    if not separator:
        raise ConfigurationError("Use provider/model or litellm_proxy/alias for Python harnesses")
    kwargs = dict(profile.model_kwargs)
    if "api_base" in kwargs:
        kwargs["base_url"] = kwargs.pop("api_base")
    kwargs.setdefault("max_retries", 0)
    if provider in ("openai", "litellm_proxy"):
        from langchain_openai import ChatOpenAI

        known = set(ChatOpenAI.model_fields)
        known.update(field.alias for field in ChatOpenAI.model_fields.values() if field.alias)
        unknown = kwargs.keys() - known
        if unknown:
            raise ConfigurationError(
                f"Unsupported DeepAgents OpenAI model settings: {sorted(unknown)}"
            )
        timeout = kwargs.get("timeout", kwargs.get("request_timeout", 600))
        if "http_client" not in kwargs:
            kwargs["http_client"] = stack.enter_context(httpx.Client(timeout=timeout))
        if "http_async_client" not in kwargs:
            kwargs["http_async_client"] = await stack.enter_async_context(
                httpx.AsyncClient(timeout=timeout)
            )
        return ChatOpenAI(model=name, **kwargs)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        model = ChatAnthropic(model_name=name, **kwargs)
        stack.callback(model._client.close)
        stack.push_async_callback(model._async_client.close)
        return model
    from langchain.chat_models import init_chat_model

    return init_chat_model(name, model_provider=provider, **kwargs)
