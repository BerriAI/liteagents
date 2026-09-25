from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import suppress
from typing import Any

import claude_agent_sdk as sdk

from ..errors import ConfigurationError, HarnessError
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

_NATIVE_TOOLS = {"read_file": "Read", "edit_file": "Edit", "run_tests": "Bash"}


def custom_tool(tool: Tool) -> Any:
    async def execute(arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await tool.execute(arguments)
            return {
                "content": [{"type": "text", "text": result}] if isinstance(result, str) else result
            }
        except Exception as exc:  # noqa: BLE001 - tool exceptions become native MCP error results
            return {"content": [{"type": "text", "text": str(exc)}], "isError": True}

    return sdk.SdkMcpTool(tool.name, tool.description, tool.input_schema, execute)


def normalize_content(content: Any) -> list[Any]:
    if isinstance(content, str):
        return [TextBlock(content)]
    blocks: list[Any] = []
    for item in content:
        if isinstance(item, dict):
            kind = item.get("type")
            if kind == "text":
                blocks.append(TextBlock(item["text"]))
            elif kind == "tool_use":
                blocks.append(ToolUseBlock(item["id"], item["name"], item["input"]))
            elif kind == "tool_result":
                blocks.append(
                    ToolResultBlock(item["tool_use_id"], item.get("content"), item.get("is_error"))
                )
        elif isinstance(item, sdk.TextBlock):
            blocks.append(TextBlock(item.text))
        elif isinstance(item, sdk.ToolUseBlock):
            blocks.append(ToolUseBlock(item.id, item.name, item.input))
        elif isinstance(item, sdk.ToolResultBlock):
            blocks.append(ToolResultBlock(item.tool_use_id, item.content, item.is_error))
    return blocks


class ClaudeAdapter(HarnessAdapter):
    allowed_options = frozenset(
        {
            "permission_mode",
            "allowed_tools",
            "disallowed_tools",
            "cli_path",
            "env",
            "max_budget_usd",
            "setting_sources",
            "timeout_seconds",
            "stderr",
            "sandbox",
        }
    )

    def validate(self) -> None:
        super().validate()
        unknown = self.profile.model_kwargs.keys() - {"api_base", "api_key", "reasoning_effort"}
        if unknown:
            raise ConfigurationError(f"Unsupported Claude model settings: {sorted(unknown)}")
        for server in self.profile.mcp_servers.values():
            unknown = server.keys() - {"command", "args", "env", "url", "headers", "transport"}
            if unknown:
                raise ConfigurationError(f"Unsupported Claude MCP settings: {sorted(unknown)}")

    async def open(self) -> None:
        options = dict(self.profile.harness_options)
        options.pop("timeout_seconds", None)
        env = dict(options.pop("env", {}))
        kwargs = self.profile.model_kwargs
        if "api_base" in kwargs:
            env["ANTHROPIC_BASE_URL"] = kwargs["api_base"].rstrip("/").removesuffix("/v1")
        if "api_key" in kwargs:
            env["ANTHROPIC_API_KEY"] = kwargs["api_key"]
        servers: dict[str, Any] = {}
        for name, config in self.profile.mcp_servers.items():
            config = dict(config)
            if "url" in config:
                config["type"] = config.pop("transport", "http")
            servers[name] = config
        registered = {tool.name: tool for tool in self.tools}
        if len(registered) != len(self.tools):
            raise ConfigurationError("Duplicate registered tool names")
        selected = [registered[name] for name in self.profile.tools if name in registered]
        if not self.profile.tools:
            selected = self.tools
        if selected:
            if "liteagents" in servers:
                raise ConfigurationError(
                    "The MCP server name 'liteagents' is reserved for custom tools"
                )
            servers["liteagents"] = sdk.create_sdk_mcp_server(
                name="liteagents", tools=[custom_tool(t) for t in selected]
            )
        native = [
            _NATIVE_TOOLS.get(name, name)
            for name in self.profile.tools
            if name not in registered and not name.startswith("mcp__")
        ]
        allowed = list(options.pop("allowed_tools", []))
        allowed += native + [f"mcp__liteagents__{tool.name}" for tool in selected]
        allowed += [name for name in self.profile.tools if name.startswith("mcp__")]
        model = self.profile.model
        if model.startswith(("anthropic/", "litellm_proxy/")):
            model = model.split("/", 1)[1]
        elif "/" in model:
            raise ConfigurationError(
                "Claude requires an Anthropic-compatible model; use a compatible gateway alias"
            )
        native_options = sdk.ClaudeAgentOptions(
            model=model,
            cwd=str(self.cwd),
            env=env,
            mcp_servers=servers,
            strict_mcp_config=True,
            tools=native if self.profile.tools else None,
            allowed_tools=allowed,
            system_prompt=self.profile.system_prompt,
            max_turns=(self.profile.max_turns or 20),
            include_partial_messages=self.profile.features.streaming,
            resume=self.session_id if self.resume_session else None,
            effort=kwargs.get("reasoning_effort"),
            **options,
        )
        self.client = sdk.ClaudeSDKClient(options=native_options)
        await self.client.__aenter__()
        self._connected = True

    async def close(self) -> None:
        if getattr(self, "_connected", False):
            self._connected = False
            await self.client.__aexit__(None, None, None)

    async def query(
        self, prompt: str, *, run_id: str, resume: bool = False
    ) -> AsyncGenerator[AgentEvent, None]:
        completed = False
        final_message = None
        try:
            async with asyncio.timeout(self.profile.harness_options.get("timeout_seconds", 300)):
                await self.client.query(prompt)
                async for message in self.client.receive_response():
                    if isinstance(message, sdk.SystemMessage):
                        self.native_session_id = message.data.get(
                            "session_id", self.native_session_id
                        )
                    elif isinstance(message, sdk.AssistantMessage):
                        if getattr(message, "error", None):
                            raise HarnessError(f"Claude model error: {message.error}")
                        blocks = normalize_content(message.content)
                        if blocks:
                            stop = (
                                "tool_use"
                                if any(isinstance(b, ToolUseBlock) for b in blocks)
                                else "end_turn"
                            )
                            final_message = AssistantMessage(
                                blocks, message.model, stop, getattr(message, "usage", None)
                            )
                            yield final_message
                    elif isinstance(message, sdk.UserMessage):
                        blocks = normalize_content(message.content)
                        if blocks and any(isinstance(b, ToolResultBlock) for b in blocks):
                            yield UserMessage(blocks)
                    elif isinstance(message, sdk.StreamEvent) and self.profile.features.streaming:
                        event = message.event
                        if (
                            event.get("type") == "content_block_delta"
                            and event.get("delta", {}).get("type") == "text_delta"
                        ):
                            yield TextDelta(event["delta"]["text"], self.profile.model)
                    elif isinstance(message, sdk.ResultMessage):
                        self.native_session_id = message.session_id
                        completed = True
                        if message.is_error:
                            raise HarnessError(
                                f"Claude run failed ({message.subtype}): {message.result or ''}"
                            )
                        completed = True
                        if final_message is not None and not final_message.usage:
                            final_message.usage = message.usage
                        if message.result and (
                            final_message is None or final_message.stop_reason == "tool_use"
                        ):
                            yield AssistantMessage(
                                [TextBlock(message.result)],
                                self.profile.model,
                                "end_turn",
                                message.usage,
                            )
                if not completed:
                    raise HarnessError("Claude stream ended without a result")
        finally:
            if not completed and getattr(self, "_connected", False):
                with suppress(Exception):
                    await self.client.interrupt()
