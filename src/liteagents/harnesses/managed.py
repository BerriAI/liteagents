"""Run a native CLI's own loop behind durable provider and MCP boundaries."""

from __future__ import annotations

from contextlib import AsyncExitStack, aclosing
from importlib import import_module
from uuid import uuid4

from ..errors import ConfigurationError, MissingDependencyError, UnsupportedFeatureError
from ..profiles import FeatureOptions
from ..runtime.control import CURRENT
from ..runtime.delegation import delegate_tools
from ..runtime.native_gateway import NativeGateway
from ..runtime.tool_names import normalize_tool_names
from ..runtime.tooling import load_servers, select_tools
from ..storage.store import digest
from .base import HarnessAdapter


class ManagedAdapter(HarnessAdapter):
    def validate(self):
        if bool(self.profile.subagents) != self.profile.features.subagents:
            raise ConfigurationError("Set features.subagents=true with at least one named subagent")
        if self.profile.tools == [] and self.profile.features.subagents:
            raise ConfigurationError("tools=[] disables tools; remove subagents or select tools")
        if not self.profile.model_kwargs.get("api_base"):
            raise ConfigurationError(
                "Shared tools, MCP, explicit tool selection, and recovery on native CLI harnesses "
                "require model_kwargs.api_base (your compatible model endpoint)"
            )
        if self.resume_session and self.profile.temporal:
            raise UnsupportedFeatureError(
                "Attach to durable runs with get_run; native session resume is independent"
            )
        forbidden = {
            "base_url",
            "config",
            "allowed_tools",
            "disallowed_tools",
            "setting_sources",
        } & self.profile.harness_options.keys()
        if forbidden:
            raise ConfigurationError(
                f"Managed native runs own provider/tool configuration; unsupported overrides: {sorted(forbidden)}"
            )
        # Validate the underlying adapter before opening any MCP or provider connection.
        options = dict(self.profile.harness_options)
        options.pop("interrupt_on", None)
        options.pop("subagent_model_instances", None)
        native_profile = self.profile.model_copy(update={
            "temporal": None, "recovery": None, "tools": None, "mcp_servers": {},
            "harness_options": options, "features": FeatureOptions(), "subagents": {},
            "max_turns": None if self.profile.harness == "codex" else self.profile.max_turns,
        })
        self.native_factory()(
            native_profile, cwd=self.cwd, tools=[], session_id=self.session_id
        )

    def native_factory(self):
        harness = self.profile.harness
        module, cls = {
            "claude-sdk": ("claude_sdk", "ClaudeAdapter"),
            "codex": ("codex", "CodexAdapter"),
            "opencode-v1": ("opencode", "OpenCodeAdapter"),
            "opencode-v2": ("opencode", "OpenCodeAdapter"),
        }[harness]
        try:
            return getattr(import_module("liteagents.harnesses." + module), cls)
        except ModuleNotFoundError as exc:
            raise MissingDependencyError(
                f"Install liteagents[{harness}] to use {harness}; missing {exc.name}"
            ) from exc

    async def open(self):
        self.stack = AsyncExitStack()
        registered = self.tools + await load_servers(self.profile, self.stack)
        delegates = delegate_tools(self.profile, self.cwd, self.tools)
        registered += delegates
        names = self.tool_allowlist
        if names is None:
            names = (
                set(self.profile.tools)
                if self.profile.tools is not None
                else {t.name for t in registered} | {"read_file", "edit_file", "run_tests"}
            )
        names |= {t.name for t in delegates}
        selected = select_tools(
            self.profile.model_copy(update={"tools": sorted(names)}), registered, self.cwd
        )
        if len({t.name for t in selected}) != len(selected):
            raise ConfigurationError("Duplicate managed tool names")
        self.tool_names = {
            native: tool.name
            for tool in selected
            for native in (
                "mcp__liteagents__" + tool.name,
                "liteagents/" + tool.name,
                "liteagents_" + tool.name,
            )
        }
        self.gateway = NativeGateway(self.profile, selected)
        self.stack.push_async_callback(self.gateway.close)
        await self.gateway.open()
        options = dict(self.profile.harness_options)
        options.pop("interrupt_on", None)
        options.pop("subagent_model_instances", None)
        kwargs = {
            **self.profile.model_kwargs,
            "api_base": self.gateway.url + "/v1",
            "api_key": "liteagents-local",
        }
        servers = {"liteagents": {"url": self.gateway.url + "/mcp"}}
        native_tools = None
        harness = self.profile.harness
        if harness == "claude-sdk":
            native_tools = ["mcp__liteagents__" + t.name for t in selected]
            options["setting_sources"] = []
        elif harness == "codex":
            servers["liteagents"]["tools"] = {
                t.name: {"approval_mode": "approve"} for t in selected
            }
            options["config"] = {
                "features": {"shell_tool": False, "multi_agent": False, "multi_agent_v2": False},
                "web_search": "disabled",
            }
            options["ephemeral"] = bool(self.profile.temporal)
        else:
            options["config"] = {"permission": {"*": "deny", "liteagents_*": "allow"}}
        if self.profile.temporal or self.tool_allowlist is not None:
            # OpenCode needs an isolated server directory per attempt. Codex and
            # Claude can share their run directory across fresh native sessions.
            identifier = (
                uuid4().hex
                if harness.startswith("opencode") and self.profile.temporal
                else digest(self.session_id)
            )
            state_dir = self.cwd / ".liteagents" / "attempts" / identifier
            if harness == "claude-sdk":
                options["env"] = {**options.get("env", {}), "CLAUDE_CONFIG_DIR": str(state_dir)}
            else:
                options["state_dir"] = str(state_dir)
            control = CURRENT.get()
            if control is not None and control.store is not None:
                await control.put(
                    "checkpoint:" + str(state_dir), {"kind": "native", "path": str(state_dir)}
                )
        native_profile = self.profile.model_copy(
            update={
                "temporal": None,
                "recovery": None,
                "model_kwargs": kwargs,
                "mcp_servers": servers,
                "tools": native_tools,
                "harness_options": options,
                "features": FeatureOptions(),
                "subagents": {},
                "max_turns": None if harness == "codex" else self.profile.max_turns,
            }
        )
        self.native = self.native_factory()(
            native_profile,
            cwd=self.cwd,
            tools=[],
            session_id=self.session_id,
            resume_session=self.resume_session,
        )
        self.stack.push_async_callback(self.native.close)
        await self.native.open()

    async def close(self):
        if hasattr(self, "stack"):
            await self.stack.aclose()

    async def query(self, prompt, *, run_id, resume=False):
        control = CURRENT.get()
        if control is None:
            raise ConfigurationError("Managed native adapter needs an active run")
        self.gateway.bind(control)
        try:
            async with aclosing(self.native.query(prompt, run_id=run_id)) as stream:
                async for event in stream:
                    if self.gateway.error:
                        raise self.gateway.error
                    yield normalize_tool_names(event, self.tool_names)
            if self.gateway.error:
                raise self.gateway.error
        except Exception:
            if self.gateway.error:
                raise self.gateway.error
            raise
        finally:
            self.native_session_id = self.native.native_session_id
