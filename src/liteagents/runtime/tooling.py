"""Workspace tools and caller-owned MCP sessions for Python harnesses."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from ..errors import ConfigurationError, MissingDependencyError
from ..mcp_config import normalize_servers
from ..profiles import ProfileOptions
from ..tools import Tool


class WorkspaceTool(Tool):
    def __init__(self, name: str, cwd: Path):
        self.name = name
        self.cwd = cwd.resolve()
        self.description = {
            "read_file": "Read a UTF-8 text file in the workspace.",
            "edit_file": "Replace one exact, unique text occurrence in a workspace file.",
            "run_tests": "Run a test command in the workspace with a bounded timeout.",
        }[name]
        properties: dict[str, Any] = {"path": {"type": "string"}}
        required = ["path"]
        if name == "edit_file":
            properties.update(old={"type": "string"}, new={"type": "string"})
            required += ["old", "new"]
        elif name == "run_tests":
            properties = {"command": {"type": "array", "items": {"type": "string"}, "minItems": 1}}
            required = ["command"]
        self.input_schema = {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }

    async def execute(self, input: dict[str, Any]) -> str:
        if self.name == "run_tests":
            process = await asyncio.create_subprocess_exec(
                *input["command"],
                cwd=self.cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                output, _ = await asyncio.wait_for(process.communicate(), timeout=120)
            except BaseException:
                if process.returncode is None:
                    process.kill()
                await process.wait()
                raise
            return f"Exit code: {process.returncode}\n{output.decode(errors='replace')[-50000:]}"
        path = (self.cwd / input["path"]).resolve()
        if not path.is_relative_to(self.cwd):
            raise ValueError("File path must remain inside the workspace")
        text = path.read_text()
        if self.name == "read_file":
            return text[:100000]
        if not input["old"] or text.count(input["old"]) != 1:
            raise ValueError("old must match exactly one nonempty occurrence")
        path.write_text(text.replace(input["old"], input["new"], 1))
        return f"Updated {input['path']}"


def select_tools(profile: ProfileOptions, registered: list[Tool], cwd: Path) -> list[Tool]:
    by_name: dict[str, Tool] = {}
    for tool in registered:
        if tool.name in by_name:
            raise ConfigurationError(f"Duplicate registered tool: {tool.name}")
        by_name[tool.name] = tool
    selected = []
    for name in profile.tools if profile.tools is not None else by_name:
        if name in by_name:
            selected.append(by_name[name])
        elif name in ("read_file", "edit_file", "run_tests"):
            selected.append(WorkspaceTool(name, cwd))
        else:
            raise ConfigurationError(f"Tool {name!r} has no registered implementation")
    return selected


async def load_servers(profile: ProfileOptions, stack: AsyncExitStack) -> list[Tool]:
    if not profile.mcp_servers:
        return []
    try:
        servers = normalize_servers(profile.mcp_servers)
    except ValueError as exc:
        raise ConfigurationError(str(exc)) from exc
    try:
        from mcp import ClientSession, StdioServerParameters
    except ImportError as exc:
        raise MissingDependencyError("Install liteagents[mcp] to use MCP servers") from exc
    from mcp.client import streamable_http as transport_module
    from mcp.client.sse import sse_client
    from mcp.client.stdio import stdio_client

    from ..mcp import load_mcp_tools

    tools: list[Tool] = []
    for name, config in servers.items():
        unknown = config.keys() - {
            "url",
            "command",
            "args",
            "env",
            "headers",
            "transport",
            "allowed_tools",
        }
        if unknown:
            raise ConfigurationError(f"Unknown MCP settings for {name}: {sorted(unknown)}")
        if "command" in config and "url" not in config:
            channels = await stack.enter_async_context(
                stdio_client(
                    StdioServerParameters(
                        command=config["command"],
                        args=config.get("args", []),
                        env=config.get("env"),
                    )
                )
            )
        elif "url" in config and "command" not in config:
            transport = config.get("transport", "http")
            if transport not in ("http", "sse"):
                raise ConfigurationError(f"Unknown MCP transport {transport!r}")
            if transport == "sse":
                connection = sse_client(config["url"], headers=config.get("headers"))
            elif hasattr(transport_module, "streamable_http_client"):
                # MCP 2.x uses httpx2; construct the transport's matching client.
                module: Any = transport_module
                http = getattr(module, "httpx2", None) or module.httpx
                client = await stack.enter_async_context(
                    http.AsyncClient(headers=config.get("headers"))
                )
                connection = module.streamable_http_client(config["url"], http_client=client)
            else:
                legacy_module: Any = transport_module
                connection = legacy_module.streamablehttp_client(
                    config["url"], headers=config.get("headers")
                )
            channels = await stack.enter_async_context(connection)
        else:
            raise ConfigurationError(f"MCP server {name} needs exactly one of url or command")
        session = await stack.enter_async_context(ClientSession(channels[0], channels[1]))
        await session.initialize()
        tools.extend(
            await load_mcp_tools(
                session, prefix=f"{name}_", allowed_tool_names=config.get("allowed_tools")
            )
        )
    return tools
