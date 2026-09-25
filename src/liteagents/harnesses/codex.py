from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncGenerator
from contextlib import aclosing, suppress
from pathlib import Path
from typing import Any, cast

from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox

from ..errors import ConfigurationError, HarnessError, UnsupportedFeatureError
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


def item_tool(item: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    kind = item.get("type")
    if kind == "commandExecution":
        return "exec_command", {"command": item.get("command"), "cwd": item.get("cwd")}
    if kind == "fileChange":
        return "file_change", {"changes": item.get("changes", [])}
    if kind == "mcpToolCall":
        return f"{item.get('server')}/{item.get('tool')}", item.get("arguments", {})
    if kind == "webSearch":
        return "web_search", {"query": item.get("query", "")}
    return None


class CodexAdapter(HarnessAdapter):
    allowed_options = frozenset(
        {
            "sandbox",
            "approval_mode",
            "config",
            "env",
            "codex_bin",
            "timeout_seconds",
            "ephemeral",
            "state_dir",
        }
    )

    def validate(self) -> None:
        super().validate()
        if self.profile.tools:
            raise UnsupportedFeatureError(
                "Codex tool selection uses native config and MCP; profile.tools is not supported"
            )
        unknown = self.profile.model_kwargs.keys() - {"api_base", "api_key", "reasoning_effort"}
        if unknown:
            raise ConfigurationError(f"Unsupported Codex model settings: {sorted(unknown)}")
        if self.profile.max_turns is not None:
            raise UnsupportedFeatureError(
                "Codex exposes no model-call limit; use harness_options.timeout_seconds"
            )
        for server in self.profile.mcp_servers.values():
            unknown = server.keys() - {
                "command",
                "args",
                "env",
                "url",
                "http_headers",
                "bearer_token_env_var",
                "enabled",
                "startup_timeout_sec",
                "tool_timeout_sec",
                "enabled_tools",
                "disabled_tools",
                "tools",
                "default_tools_approval_mode",
            }
            if unknown:
                raise ConfigurationError(
                    f"Unsupported native Codex MCP settings: {sorted(unknown)}"
                )

    async def open(self) -> None:
        options = self.profile.harness_options
        env = dict(os.environ)
        env.update(options.get("env", {}))
        # Give the child runtime its own native state/config directory. Resuming
        # uses the same workspace (or an explicit persistent state_dir).
        state_dir = Path(options.get("state_dir", self.cwd / ".liteagents" / "codex")).resolve()
        state_dir.mkdir(parents=True, exist_ok=True)
        env["CODEX_HOME"] = str(state_dir)
        config = dict(options.get("config", {}))
        model = self.profile.model
        kwargs = self.profile.model_kwargs
        provider = None
        if "api_base" in kwargs:
            provider = "liteagents"
            provider_options: dict[str, Any] = {
                "name": "LiteAgents gateway",
                "base_url": kwargs["api_base"],
                "wire_api": "responses",
            }
            if "api_key" in kwargs:
                env["LITEAGENTS_CODEX_API_KEY"] = kwargs["api_key"]
                provider_options["env_key"] = "LITEAGENTS_CODEX_API_KEY"
            config["model_providers"] = {
                **config.get("model_providers", {}),
                provider: provider_options,
            }
        elif "api_key" in kwargs:
            env["OPENAI_API_KEY"] = kwargs["api_key"]
        if model.startswith(("openai/", "litellm_proxy/")):
            model = model.split("/", 1)[1]
        elif "/" in model:
            raise ConfigurationError("Codex needs a Responses-compatible model/provider")
        if self.profile.mcp_servers:
            config["mcp_servers"] = {**config.get("mcp_servers", {}), **self.profile.mcp_servers}
        self.client = AsyncCodex(
            CodexConfig(codex_bin=options.get("codex_bin"), cwd=str(self.cwd), env=env)
        )
        await self.client.__aenter__()
        self._connected = True
        thread_options: dict[str, Any] = {
            "model": model,
            "model_provider": provider,
            "cwd": str(self.cwd),
            "config": config,
            "base_instructions": self.profile.system_prompt,
            "sandbox": Sandbox(options.get("sandbox", "workspace-write")),
            "approval_mode": ApprovalMode(options.get("approval_mode", "deny_all")),
        }
        if self.resume_session:
            self.thread = await self.client.thread_resume(self.session_id, **thread_options)
        else:
            self.thread = await self.client.thread_start(
                ephemeral=options.get("ephemeral", False), **thread_options
            )
        self.native_session_id = self.thread.id

    async def close(self) -> None:
        if getattr(self, "_connected", False):
            self._connected = False
            await self.client.__aexit__(None, None, None)

    async def query(
        self, prompt: str, *, run_id: str, resume: bool = False
    ) -> AsyncGenerator[AgentEvent, None]:
        completed = False
        turn = None
        final_message = None
        usage = None
        tool_ids: set[str] = set()
        try:
            async with asyncio.timeout(self.profile.harness_options.get("timeout_seconds", 300)):
                turn = await self.thread.turn(
                    prompt, effort=self.profile.model_kwargs.get("reasoning_effort")
                )
                async with aclosing(cast(AsyncGenerator[Any, None], turn.stream())) as stream:
                    async for notification in stream:
                        payload = notification.payload
                        data: Any = (
                            payload.model_dump(mode="json", by_alias=True)
                            if hasattr(payload, "model_dump")
                            else payload.params
                        )
                        method = notification.method
                        if method == "item/agentMessage/delta" and self.profile.features.streaming:
                            yield TextDelta(data["delta"], self.profile.model)
                        elif method == "thread/tokenUsage/updated":
                            usage = data.get("tokenUsage", {}).get("last")
                        elif method in ("item/started", "item/completed"):
                            item = data["item"]
                            tool = item_tool(item)
                            if tool:
                                if item["id"] not in tool_ids:
                                    tool_ids.add(item["id"])
                                    yield AssistantMessage(
                                        [ToolUseBlock(item["id"], *tool)],
                                        self.profile.model,
                                        "tool_use",
                                    )
                                if method == "item/completed":
                                    output = item.get(
                                        "aggregatedOutput",
                                        item.get("result", item.get("changes", "")),
                                    )
                                    if not isinstance(output, (str, list)):
                                        output = json.dumps(output)
                                    is_error = (
                                        item.get("status") in ("failed", "declined")
                                        or item.get("error") is not None
                                    )
                                    yield UserMessage(
                                        [ToolResultBlock(item["id"], output, is_error)]
                                    )
                            elif method == "item/completed" and item.get("type") == "agentMessage":
                                final_message = AssistantMessage(
                                    [TextBlock(item.get("text", ""))],
                                    self.profile.model,
                                    "end_turn" if item.get("phase") != "commentary" else None,
                                )
                                yield final_message
                        elif method == "turn/completed":
                            completed = True
                            status = data["turn"]["status"]
                            if status != "completed":
                                raise HarnessError(
                                    f"Codex turn {status}: {data['turn'].get('error')}"
                                )
                if not completed:
                    raise HarnessError("Codex stream ended without a completed turn")
                if final_message is None:
                    raise HarnessError("Codex completed without an assistant response")
                if final_message is not None:
                    final_message.usage = usage
        finally:
            if turn is not None and not completed:
                with suppress(Exception):
                    await turn.interrupt()
