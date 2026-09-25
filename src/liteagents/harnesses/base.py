from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any

from ..errors import MissingDependencyError, UnsupportedFeatureError
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


_ADAPTERS = {
    "deepagents": (
        "deepagents",
        "DeepAgentsAdapter",
        HarnessCapabilities(temporal=True, recovery="graph"),
    ),
    "pydantic-ai": ("pydantic_ai", "PydanticAIAdapter", HarnessCapabilities(recovery="memory")),
    "claude-sdk": ("claude_sdk", "ClaudeAdapter", HarnessCapabilities()),
    "codex": ("codex", "CodexAdapter", HarnessCapabilities(custom_tools=False)),
    "opencode-v1": ("opencode", "OpenCodeAdapter", HarnessCapabilities(custom_tools=False)),
    "opencode-v2": ("opencode", "OpenCodeAdapter", HarnessCapabilities(custom_tools=False)),
}


def available_harnesses() -> tuple[str, ...]:
    return tuple(_ADAPTERS)


def get_capabilities(name: str) -> HarnessCapabilities:
    if name not in _ADAPTERS:
        raise UnsupportedFeatureError(f"Unknown harness {name!r}")
    return _ADAPTERS[name][2]


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
    ):
        self.profile = profile
        self.cwd = cwd
        self.tools = tools
        self.session_id = session_id
        self.resume_session = resume_session
        self.native_session_id: str | None = None
        self.validate()

    def validate(self) -> None:
        capabilities = get_capabilities(self.profile.harness)
        if self.profile.subagents or self.profile.features.subagents:
            raise UnsupportedFeatureError("Subagent configuration is a milestone 3 feature")
        if self.profile.recovery is not None:
            raise UnsupportedFeatureError(
                "Per-operation retries/model/harness fallbacks are a milestone 3 feature; "
                "Temporal worker recovery is configured separately"
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
    module, name, _ = _ADAPTERS[profile.harness]
    try:
        cls = getattr(import_module(f"liteagents.harnesses.{module}"), name)
        return cls(profile, **kwargs)
    except ModuleNotFoundError as exc:
        raise MissingDependencyError(
            f"Install liteagents[{profile.harness}] to use {profile.harness}; missing {exc.name}"
        ) from exc
