from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass, replace
from importlib import import_module
from pathlib import Path
from typing import Any

from ..errors import MissingDependencyError, UnsupportedFeatureError
from ..function_tools import ToolInput
from ..profiles import ProfileOptions
from ..tools import Tool
from ..types import AgentEvent


@dataclass(frozen=True)
class HarnessCapabilities:
    streaming: bool = True
    custom_tools: bool = True
    mcp: bool = True
    temporal: bool = False
    recovery: str = "session"
    execution_mode: str = "native"


_ADAPTERS = {
    "deepagents": (
        "deepagents",
        "DeepAgentsAdapter",
        HarnessCapabilities(temporal=True, recovery="graph"),
    ),
    "pydantic-ai": (
        "pydantic_ai",
        "PydanticAIAdapter",
        HarnessCapabilities(temporal=True, recovery="operations"),
    ),
    "claude-sdk": (
        "claude_sdk",
        "ClaudeAdapter",
        HarnessCapabilities(temporal=True),
    ),
    "codex": (
        "codex",
        "CodexAdapter",
        HarnessCapabilities(custom_tools=False, temporal=True),
    ),
    "opencode-v1": (
        "opencode",
        "OpenCodeAdapter",
        HarnessCapabilities(custom_tools=False, temporal=True),
    ),
    "opencode-v2": (
        "opencode",
        "OpenCodeAdapter",
        HarnessCapabilities(custom_tools=False, temporal=True),
    ),
}


def available_harnesses() -> tuple[str, ...]:
    return tuple(_ADAPTERS)


def uses_managed_execution(profile: ProfileOptions, *, tools: Sequence[ToolInput] = ()) -> bool:
    options = profile.native_options()
    attached = profile.harness.startswith("opencode") and options.get("base_url")
    shared_model = "/" in profile.model or any(
        profile.model_kwargs.get(name) for name in ("api_base", "custom_llm_provider")
    )
    return profile.harness not in ("deepagents", "pydantic-ai") and bool(
        (shared_model and not attached)
        or profile.temporal
        or profile.recovery
        or profile.tools is not None
        or profile.mcp_servers
        or tools
        or options.get("interrupt_on")
        or profile.features.subagents
        or profile.subagents
    )


def get_capabilities(
    profile: str | ProfileOptions, *, tools: Sequence[ToolInput] = ()
) -> HarnessCapabilities:
    """Pass a profile and registered tools for the effective execution capabilities.

    A harness name alone describes its ordinary native adapter.
    """
    name = profile if isinstance(profile, str) else profile.harness
    if name not in _ADAPTERS:
        raise UnsupportedFeatureError(f"Unknown harness {name!r}")
    result = _ADAPTERS[name][2]
    if isinstance(profile, ProfileOptions) and uses_managed_execution(profile, tools=tools):
        return replace(result, custom_tools=True, recovery="operations", execution_mode="managed")
    return result


class HarnessAdapter(ABC):
    allowed_options: frozenset[str] = frozenset()

    def __init__(
        self,
        profile: ProfileOptions,
        *,
        cwd: Path,
        tools: list[Tool],
        session_id: str,
        resume_session: bool = False,
        tool_allowlist: set[str] | None = None,
        history: list | None = None,
    ):
        self.profile = profile
        self.cwd = cwd
        self.tools = tools
        self.session_id = session_id
        self.tool_allowlist = tool_allowlist
        self.history = history or []
        self.resume_session = resume_session
        self.native_session_id: str | None = None
        self.validate()

    def validate(self) -> None:
        capabilities = get_capabilities(self.profile.harness)
        if self.profile.features.subagents and not self.profile.subagents:
            raise UnsupportedFeatureError(
                "Configure at least one named subagent"
            )
        if self.profile.tools == [] and self.profile.subagents:
            raise UnsupportedFeatureError("tools=[] disables tools; remove subagents or select tools")
        if self.profile.recovery is not None and self.profile.harness not in (
            "deepagents",
            "pydantic-ai",
        ):
            raise UnsupportedFeatureError(
                "Native operation recovery requires the managed execution adapter"
            )
        if self.profile.temporal and not capabilities.temporal:
            raise UnsupportedFeatureError(
                f"{self.profile.harness} has no verified Temporal recovery adapter yet; "
                "use direct execution or deepagents for durable runs"
            )
        if self.tools and not capabilities.custom_tools:
            raise UnsupportedFeatureError(
                f"Use MCP to expose custom tools to {self.profile.harness}"
            )
        unknown = self.profile.harness_options.keys() - self.allowed_options
        if unknown:
            raise UnsupportedFeatureError(
                f"Unsupported {self.profile.harness} options: {sorted(unknown)}"
            )

    async def open(self) -> None:
        pass

    async def close(self) -> None:
        pass

    @abstractmethod
    def query(
        self, prompt: str, *, run_id: str, resume: bool = False
    ) -> AsyncGenerator[AgentEvent, None]:
        raise NotImplementedError


def create_adapter(profile: ProfileOptions, **kwargs: Any) -> HarnessAdapter:
    profile = profile.model_copy(update={"harness_options": profile.native_options()})
    if uses_managed_execution(profile, tools=kwargs.get("tools", ())):
        from .managed import ManagedAdapter

        return ManagedAdapter(profile, **kwargs)
    module, name, _ = _ADAPTERS[profile.harness]
    try:
        cls = getattr(import_module(f"liteagents.harnesses.{module}"), name)
        return cls(profile, **kwargs)
    except ModuleNotFoundError as exc:
        raise MissingDependencyError(
            f"Install liteagents[{profile.harness}] to use {profile.harness}; missing {exc.name}"
        ) from exc
