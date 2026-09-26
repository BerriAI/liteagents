from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.tools import Tool as NativeTool
from pydantic_ai.usage import UsageLimits

from ..errors import ConfigurationError, HarnessError, UnsupportedFeatureError
from ..runtime.control import CURRENT
from ..runtime.delegation import delegate_tools
from ..runtime.tooling import load_servers, select_tools
from ..tools import Tool
from ..types import (
    AgentEvent,
    AssistantMessage,
    TextBlock,
    TextDelta,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from .base import HarnessAdapter
from .pydantic_control import ControlledModel


def wrap_tool(tool: Tool) -> NativeTool:
    async def execute(ctx: Any, **kwargs: Any) -> Any:
        control = CURRENT.get()

        async def invoke():
            return await tool.execute(kwargs)

        if control is None:
            return await invoke()
        return await control.call(
            "tool", ctx.tool_call_id, {"name": tool.name, "input": kwargs}, invoke
        )

    return NativeTool.from_schema(
        execute,
        name=tool.name,
        description=tool.description,
        json_schema=tool.input_schema,
        takes_ctx=True,
    )


def build_model(profile: Any) -> tuple[Any, dict[str, Any]]:
    if "model_instance" in profile.harness_options:
        if profile.model_kwargs:
            raise ConfigurationError("Configure model_kwargs on the supplied model_instance")
        return profile.harness_options["model_instance"], {}
    provider, separator, name = profile.model.partition("/")
    if not separator:
        raise ConfigurationError("Use provider/model or litellm_proxy/alias for Python harnesses")
    kwargs = dict(profile.model_kwargs)
    base_url = kwargs.pop("api_base", None)
    api_key = kwargs.pop("api_key", None)
    if "stop" in kwargs:
        stop = kwargs.pop("stop")
        kwargs["stop_sequences"] = [stop] if isinstance(stop, str) else stop
    if provider in ("openai", "litellm_proxy") and "reasoning_effort" in kwargs:
        kwargs["openai_reasoning_effort"] = kwargs.pop("reasoning_effort")
    from pydantic_ai.models.anthropic import AnthropicModelSettings
    from pydantic_ai.models.openai import OpenAIChatModelSettings

    known = (
        AnthropicModelSettings if provider == "anthropic" else OpenAIChatModelSettings
    ).__annotations__
    unknown = kwargs.keys() - known.keys()
    if unknown:
        raise ConfigurationError(f"Unsupported Pydantic AI model settings: {sorted(unknown)}")
    if provider in ("openai", "litellm_proxy"):
        from openai import AsyncOpenAI

        client_kwargs = {
            key: value
            for key, value in {"base_url": base_url, "api_key": api_key}.items()
            if value is not None
        }
        client = AsyncOpenAI(max_retries=0, **client_kwargs)
        model: Any = OpenAIChatModel(name, provider=OpenAIProvider(openai_client=client))
    elif provider == "anthropic":
        from anthropic import AsyncAnthropic
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        client_kwargs = {
            key: value
            for key, value in {"base_url": base_url, "api_key": api_key}.items()
            if value is not None
        }
        model = AnthropicModel(
            name,
            provider=AnthropicProvider(
                anthropic_client=AsyncAnthropic(max_retries=0, **client_kwargs)
            ),
        )
    else:
        raise ConfigurationError(
            "This adapter supports openai/, anthropic/, and litellm_proxy/; use a native model_instance for other providers"
        )
    return model, kwargs


def convert_messages(messages: list[Any], model: str) -> list[AgentEvent]:
    events: list[AgentEvent] = []
    for message in messages:
        if isinstance(message, ModelResponse):
            blocks: list[Any] = []
            for part in message.parts:
                if isinstance(part, TextPart):
                    blocks.append(TextBlock(part.content))
                elif isinstance(part, ToolCallPart):
                    blocks.append(
                        ToolUseBlock(part.tool_call_id, part.tool_name, part.args_as_dict())
                    )
            if blocks:
                uses_tools = any(isinstance(block, ToolUseBlock) for block in blocks)
                usage = {
                    "input_tokens": message.usage.input_tokens,
                    "output_tokens": message.usage.output_tokens,
                }
                events.append(
                    AssistantMessage(blocks, model, "tool_use" if uses_tools else "end_turn", usage)
                )
        elif isinstance(message, ModelRequest):
            for request_part in message.parts:
                if isinstance(request_part, ToolReturnPart):
                    content = (
                        request_part.content
                        if isinstance(request_part.content, (str, list))
                        else str(request_part.content)
                    )
                    events.append(
                        UserMessage([ToolResultBlock(request_part.tool_call_id, content)])
                    )
    return events


class PydanticAIAdapter(HarnessAdapter):
    allowed_options = frozenset(
        {
            "model_instance",
            "tool_timeout",
            "interrupt_on",
            "fallback_model_instances",
            "subagent_model_instances",
        }
    )

    def validate(self) -> None:
        super().validate()
        if self.resume_session:
            raise UnsupportedFeatureError(
                "Pydantic AI history lasts for one client; session resume is not supported"
            )
        if "model_instance" not in self.profile.harness_options:
            from ..runtime.model_bridge import validate_settings

            validate_settings(self.profile)

    async def open(self) -> None:
        self.stack = AsyncExitStack()
        registered = self.tools + await load_servers(self.profile, self.stack)
        delegates = delegate_tools(self.profile, self.cwd, self.tools)
        registered += delegates
        names = self.tool_allowlist
        if names is None and self.profile.tools is not None:
            names = set(self.profile.tools)
        if names is not None:
            names = names | {t.name for t in delegates}
        selected = (
            select_tools(
                self.profile.model_copy(update={"tools": sorted(names)}), registered, self.cwd
            )
            if names is not None
            else registered
        )

        from ..runtime.model_endpoint import translated_profile

        local = self.profile
        if "model_instance" not in local.harness_options:
            local = await translated_profile(local, self.stack)
        model, settings = build_model(local)
        if "model_instance" not in self.profile.harness_options:
            self.stack.push_async_callback(model.client.close)
        models = [(self.profile.model, model, settings)]
        fallbacks = self.profile.recovery.model_fallbacks if self.profile.recovery else []
        supplied = self.profile.harness_options.get("fallback_model_instances", [])
        for index, name in enumerate(fallbacks):
            native = dict(self.profile.harness_options)
            native.pop("model_instance", None)
            if index < len(supplied):
                native["model_instance"] = supplied[index]
            alternative = self.profile.model_copy(update={"model": name, "harness_options": native})
            if "model_instance" not in native:
                alternative = await translated_profile(alternative, self.stack)
            other, other_settings = build_model(alternative)
            if "model_instance" not in native:
                self.stack.push_async_callback(other.client.close)
            models.append((name, other, other_settings))
        model = ControlledModel(models)
        agent_factory: Any = Agent
        self.agent: Any = agent_factory(
            model,
            instructions=self.profile.system_prompt,
            name="liteagents",
            tools=[wrap_tool(tool) for tool in selected],
            model_settings=settings,
            retries=0,
            tool_timeout=self.profile.harness_options.get("tool_timeout"),
        )
        await self.stack.enter_async_context(self.agent)
        from ..runtime.conversation import pydantic_history

        self.messages: list[Any] = pydantic_history(self.history)

    async def close(self) -> None:
        if hasattr(self, "stack"):
            await self.stack.aclose()

    async def query(
        self, prompt: str, *, run_id: str, resume: bool = False
    ) -> AsyncGenerator[AgentEvent, None]:
        kwargs = {
            "message_history": self.messages,
            "usage_limits": UsageLimits(request_limit=(self.profile.max_turns or 20)),
        }
        try:
            if self.profile.features.streaming:
                from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPartDelta
                from pydantic_ai.run import AgentRunResultEvent

                result = None
                control = CURRENT.get()
                journal_stream = control is not None and bool(
                    control.store or control.profile.recovery
                )
                async with self.agent.run_stream_events(prompt, **kwargs) as stream:
                    async for event in stream:
                        if (
                            not journal_stream
                            and isinstance(event, PartStartEvent)
                            and isinstance(event.part, TextPart)
                        ):
                            yield TextDelta(event.part.content, self.profile.model)
                        elif (
                            not journal_stream
                            and isinstance(event, PartDeltaEvent)
                            and isinstance(event.delta, TextPartDelta)
                        ):
                            yield TextDelta(event.delta.content_delta, self.profile.model)
                        elif isinstance(event, AgentRunResultEvent):
                            result = event.result
                if result is None:
                    raise HarnessError("Pydantic AI stream ended without a completed run")
            else:
                result = await self.agent.run(prompt, **kwargs)
            self.messages = result.all_messages()
            for normalized in convert_messages(result.new_messages(), self.profile.model):
                yield normalized
        except Exception as exc:
            if isinstance(exc, HarnessError):
                raise
            raise HarnessError(f"Pydantic AI run failed: {type(exc).__name__}: {exc}") from exc
