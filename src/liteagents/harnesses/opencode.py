"""Both OpenCode SDK generations use the server HTTP/SSE protocol.

No shared server configuration is mutated. Per-profile configuration is supplied
only to a child server owned by this adapter; attached servers must be configured
by their owner.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import socket
import sys
from collections.abc import AsyncGenerator
from contextlib import aclosing, suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from filelock import FileLock, Timeout

from ..errors import ConfigurationError, HarnessError, MissingDependencyError
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

_ALIASES = {"read_file": "read", "edit_file": "edit", "run_tests": "bash"}


def normalize_messages(messages: list[dict[str, Any]], model: str) -> list[AgentEvent]:
    events: list[AgentEvent] = []
    for message in messages:
        info = message.get("info", {})
        if info.get("role") != "assistant":
            continue
        if info.get("error"):
            raise HarnessError(f"OpenCode model error: {info['error']}")
        for part in message.get("parts", []):
            if part.get("type") == "text" and part.get("text"):
                events.append(
                    AssistantMessage(
                        [TextBlock(part["text"])],
                        model,
                        "end_turn" if info.get("finish") != "tool-calls" else None,
                        info.get("tokens"),
                    )
                )
            elif part.get("type") == "tool":
                state = part["state"]
                call_id = part["callID"]
                events.append(
                    AssistantMessage(
                        [ToolUseBlock(call_id, part["tool"], state.get("input", {}))],
                        model,
                        "tool_use",
                    )
                )
                if state.get("status") in ("completed", "error"):
                    output = state.get("output", state.get("error", ""))
                    events.append(
                        UserMessage([ToolResultBlock(call_id, output, state["status"] == "error")])
                    )
    return events


async def read_sse(response: httpx.Response, queue: asyncio.Queue[dict[str, Any]]) -> None:
    data: list[str] = []
    try:
        async for line in response.aiter_lines():
            if line.startswith("data:"):
                data.append(line[5:].lstrip())
            elif not line and data:
                value = json.loads("\n".join(data))
                await queue.put(value)
                data.clear()
        await queue.put({"_error": "OpenCode event stream disconnected"})
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        await queue.put({"_error": f"OpenCode event stream failed: {type(exc).__name__}"})


class OpenCodeAdapter(HarnessAdapter):
    allowed_options = frozenset(
        {
            "base_url",
            "binary",
            "env",
            "config",
            "agent",
            "timeout_seconds",
            "username",
            "password",
            "state_dir",
        }
    )

    def validate(self) -> None:
        super().validate()
        unknown = self.profile.model_kwargs.keys() - {
            "api_base",
            "api_key",
            "temperature",
            "top_p",
            "reasoning_effort",
        }
        if unknown:
            raise ConfigurationError(f"Unsupported OpenCode model settings: {sorted(unknown)}")
        if "base_url" in self.profile.harness_options and (
            self.profile.model_kwargs
            or self.profile.mcp_servers
            or bool({"config", "env", "binary", "state_dir"} & self.profile.harness_options.keys())
            or self.profile.max_turns is not None
        ):
            raise ConfigurationError(
                "Configure models/MCP/limits on an attached OpenCode server; LiteAgents will not modify a shared server"
            )
        if "/" not in self.profile.model:
            raise ConfigurationError("OpenCode model must be provider/model")
        for server in self.profile.mcp_servers.values():
            unknown = server.keys() - {"command", "args", "env", "url", "headers"}
            if unknown:
                raise ConfigurationError(f"Unsupported OpenCode MCP settings: {sorted(unknown)}")

    def server_config(self) -> dict[str, Any]:
        config = dict(self.profile.harness_options.get("config", {}))
        provider, model = self.profile.model.split("/", 1)
        kwargs = self.profile.model_kwargs
        if "api_base" in kwargs:
            provider = "liteagents"
            model_config: dict[str, Any] = {"name": model, "tool_call": True}
            options = {"baseURL": kwargs["api_base"]}
            if "api_key" in kwargs:
                options["apiKey"] = kwargs["api_key"]
            config["provider"] = {
                **config.get("provider", {}),
                provider: {
                    "name": "LiteAgents",
                    "npm": "@ai-sdk/openai-compatible",
                    "options": options,
                    "models": {model: model_config},
                },
            }
        elif "api_key" in kwargs:
            config["provider"] = {
                **config.get("provider", {}),
                provider: {
                    **config.get("provider", {}).get(provider, {}),
                    "options": {"apiKey": kwargs["api_key"]},
                },
            }
        self.model_spec = {"providerID": provider, "modelID": model}
        agent: dict[str, Any] = {
            "description": "LiteAgents profile",
            "mode": "primary",
            "model": f"{provider}/{model}",
            "steps": (self.profile.max_turns or 20),
        }
        for key in ("temperature", "top_p"):
            if key in kwargs:
                agent[key] = kwargs[key]
        if "reasoning_effort" in kwargs:
            agent["reasoningEffort"] = kwargs["reasoning_effort"]
        if self.profile.system_prompt:
            agent["prompt"] = self.profile.system_prompt
        config["agent"] = {**config.get("agent", {}), "liteagents": agent}
        config.setdefault("share", "disabled")
        if self.profile.mcp_servers:
            servers = dict(config.get("mcp", {}))
            for name, server in self.profile.mcp_servers.items():
                if "command" in server:
                    servers[name] = {
                        "type": "local",
                        "command": [server["command"], *server.get("args", [])],
                        "environment": server.get("env", {}),
                        "enabled": True,
                    }
                elif "url" in server:
                    servers[name] = {
                        "type": "remote",
                        "url": server["url"],
                        "headers": server.get("headers", {}),
                        "enabled": True,
                    }
                else:
                    raise ConfigurationError(f"MCP server {name} needs url or command")
            config["mcp"] = servers
        return config

    async def open(self) -> None:
        options = self.profile.harness_options
        self.process = None
        self.log_task = None
        base_url = options.get("base_url")
        username = options.get("username", "opencode")
        password = options.get("password")
        self.agent_name = options.get("agent", "liteagents" if not base_url else "build")
        provider, model = self.profile.model.split("/", 1)
        self.model_spec = {"providerID": provider, "modelID": model}
        if not base_url:
            if not shutil.which(options.get("binary", "opencode")):
                raise MissingDependencyError(
                    "Install the OpenCode CLI or configure harness_options.base_url"
                )
            env = dict(os.environ)
            env.update(options.get("env", {}))
            state_dir = Path(
                options.get("state_dir", self.cwd / ".liteagents" / self.profile.harness)
            ).resolve()
            state_dir.mkdir(parents=True, exist_ok=True)
            self.state_lock = FileLock(str(state_dir / "server.lock"))
            try:
                self.state_lock.acquire(timeout=0)
            except Timeout as exc:
                raise ConfigurationError(
                    "Another OpenCode server owns this state_dir; choose a separate directory or attach with base_url"
                ) from exc
            for kind in ("DATA", "CONFIG", "CACHE", "STATE"):
                env[f"XDG_{kind}_HOME"] = str(state_dir / kind.lower())
            password = password or uuid4().hex
            env["OPENCODE_SERVER_USERNAME"] = username
            env["OPENCODE_SERVER_PASSWORD"] = password
            env["OPENCODE_CONFIG_CONTENT"] = json.dumps(self.server_config())
            # OpenCode interprets port 0 as "try 4096 first", which races when
            # two owned servers start together. Select an available port here.
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            try:
                command = [
                    options.get("binary", "opencode"),
                    "serve",
                    "--hostname",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ]
                if os.name == "posix":
                    command = [sys.executable, "-m", "liteagents.runtime.supervise", *command]
                self.process = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=asyncio.subprocess.PIPE,
                    cwd=self.cwd,
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
            except FileNotFoundError as exc:
                raise MissingDependencyError(
                    "Install the OpenCode CLI or configure harness_options.base_url"
                ) from exc
            async with asyncio.timeout(30):
                assert self.process.stdout is not None
                startup_lines: list[str] = []
                while True:
                    line = await self.process.stdout.readline()
                    if not line:
                        diagnostic = "\n".join(startup_lines)[-3000:]
                        # Native errors can quote configuration; do not echo
                        # credentials passed to the child in diagnostic output.
                        for value in env.values():
                            if len(value) >= 6:
                                diagnostic = diagnostic.replace(value, "[REDACTED]")
                        key = self.profile.model_kwargs.get("api_key")
                        if key:
                            diagnostic = diagnostic.replace(key, "[REDACTED]")
                        raise HarnessError("OpenCode server failed to start: " + diagnostic)
                    startup_lines.append(line.decode(errors="replace").strip())
                    match = re.search(rb"http://127\.0\.0\.1:\d+", line)
                    if match:
                        base_url = match.group().decode()
                        break
            self.log_task = asyncio.create_task(self._drain_logs())
        timeout = options.get("timeout_seconds", 300)
        self.http = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            auth=(username, password) if password else None,
            params={"directory": str(self.cwd)},
        )
        health = await self.request("GET", "/global/health")
        if not health.get("healthy"):
            raise HarnessError("OpenCode server is unhealthy")
        if not str(health.get("version", "")).startswith("1.18."):
            raise ConfigurationError("This adapter supports OpenCode 1.18.x; install 1.18.29")
        if self.resume_session:
            session = await self.request("GET", f"/session/{self.session_id}")
        else:
            session = await self.request("POST", "/session", json={"title": "LiteAgents"})
        self.native_session_id = session["id"]

    async def _drain_logs(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        while await self.process.stdout.read(4096):
            pass

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self.http.request(method, path, **kwargs)
        response.raise_for_status()
        try:
            return response.json() if response.content else None
        except ValueError as exc:
            raise HarnessError(
                f"OpenCode returned invalid JSON for {method} {path}; check server compatibility"
            ) from exc

    async def close(self) -> None:
        if hasattr(self, "http"):
            await self.http.aclose()
        process = getattr(self, "process", None)
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.kill()
                await process.wait()
        log_task = getattr(self, "log_task", None)
        if log_task is not None:
            log_task.cancel()
            with suppress(asyncio.CancelledError):
                await log_task
        state_lock = getattr(self, "state_lock", None)
        if state_lock is not None:
            state_lock.release()

    async def _stream_prompt(
        self, path: str, body: dict[str, Any]
    ) -> AsyncGenerator[AgentEvent, None]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        async with self.http.stream("GET", "/event", timeout=None) as response:
            response.raise_for_status()
            reader = asyncio.create_task(read_sse(response, queue))
            request = asyncio.create_task(self.request("POST", path, json=body))
            get_event = None
            try:
                while not request.done() or not queue.empty():
                    get_event = asyncio.create_task(queue.get())
                    done, _ = await asyncio.wait(
                        (request, get_event), return_when=asyncio.FIRST_COMPLETED
                    )
                    if get_event in done:
                        event = get_event.result()
                        if "_error" in event:
                            raise HarnessError(event["_error"])
                        properties = event.get("properties", {})
                        is_text_delta = (
                            event.get("type") == "message.part.delta"
                            and properties.get("field") == "text"
                        ) or (
                            event.get("type") == "message.part.updated"
                            and properties.get("part", {}).get("type") == "text"
                            and properties.get("delta")
                        )
                        if (
                            properties.get("sessionID", properties.get("part", {}).get("sessionID"))
                            == self.native_session_id
                            and is_text_delta
                        ):
                            yield TextDelta(properties["delta"], self.profile.model)
                    else:
                        get_event.cancel()
                        with suppress(asyncio.CancelledError):
                            await get_event
                await request
            finally:
                for task in (reader, request, get_event):
                    if task is not None:
                        task.cancel()
                        with suppress(asyncio.CancelledError, Exception):
                            await task

    async def query(
        self, prompt: str, *, run_id: str, resume: bool = False
    ) -> AsyncGenerator[AgentEvent, None]:
        path = f"/session/{self.native_session_id}/message"
        previous = await self.request("GET", path)
        seen = {message["info"]["id"] for message in previous}
        body: dict[str, Any] = {
            "model": self.model_spec,
            "agent": self.agent_name,
            "parts": [{"type": "text", "text": prompt}],
        }
        if self.profile.system_prompt:
            body["system"] = self.profile.system_prompt
        if self.profile.tools is not None:
            tool_ids = await self.request("GET", "/experimental/tool/ids")
            selected = {_ALIASES.get(name, name) for name in self.profile.tools}
            unknown = selected - set(tool_ids)
            if unknown:
                raise ConfigurationError(f"Unknown OpenCode tools: {sorted(unknown)}")
            body["tools"] = {name: name in selected for name in tool_ids}
        completed = False
        try:
            async with asyncio.timeout(self.profile.harness_options.get("timeout_seconds", 300)):
                if self.profile.features.streaming:
                    async with aclosing(self._stream_prompt(path, body)) as events:
                        async for event in events:
                            yield event
                else:
                    await self.request("POST", path, json=body)
                messages = await self.request("GET", path)
                new_messages = [m for m in messages if m["info"]["id"] not in seen]
                normalized = normalize_messages(new_messages, self.profile.model)
                if not any(
                    isinstance(e, AssistantMessage) and e.stop_reason == "end_turn"
                    for e in normalized
                ):
                    raise HarnessError("OpenCode ended without a final assistant response")
                for event in normalized:
                    yield event
                completed = True
        finally:
            if not completed:
                with suppress(Exception):
                    async with asyncio.timeout(5):
                        await self.request("POST", f"/session/{self.native_session_id}/abort")
