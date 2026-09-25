"""Portable profiles. Native objects remain local to the application or worker."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .errors import ConfigurationError

HarnessName = Literal[
    "deepagents", "pydantic-ai", "claude-sdk", "codex", "opencode-v1", "opencode-v2"
]


class StrictOptions(BaseModel):
    model_config = ConfigDict(
        extra="forbid", validate_assignment=True, arbitrary_types_allowed=True
    )


class FeatureOptions(StrictOptions):
    streaming: bool = False
    subagents: bool = False


class RetryOptions(StrictOptions):
    max_attempts: int = Field(default=3, ge=1, le=100)


class RecoveryOptions(StrictOptions):
    retries: RetryOptions = Field(default_factory=RetryOptions)
    model_fallbacks: list[str] = Field(default_factory=list)
    harness_fallbacks: list[HarnessName] = Field(default_factory=list)


class TemporalOptions(StrictOptions):
    address: str = Field(default="localhost:7233", min_length=1)
    namespace: str = Field(default="default", min_length=1)
    task_queue: str = Field(default="liteagents", min_length=1)
    api_key: str | None = Field(default=None, repr=False)
    tls: bool = False
    checkpoint_path: str = ".liteagents/checkpoints.sqlite"
    checkpoint_url: str | None = Field(default=None, repr=False)
    state_url: str | None = Field(default=None, repr=False)
    max_events: int = Field(default=20000, ge=10, le=1000000)
    max_payload_bytes: int = Field(default=8000000, ge=1024, le=50000000)
    retention_days: int = Field(default=30, ge=1, le=3650)
    max_concurrent_runs: int = Field(default=1, ge=1, le=100)
    tls_server_root_ca: str | None = None
    tls_client_cert: str | None = None
    tls_client_key: str | None = Field(default=None, repr=False)
    tls_server_name: str | None = None
    profile_id: str | None = Field(default=None, min_length=1)
    activity_timeout_seconds: int = Field(default=600, ge=10)
    heartbeat_timeout_seconds: int = Field(default=15, ge=2)
    worker_recovery_attempts: int = Field(default=3, ge=1, le=100)


class SubagentOptions(StrictOptions):
    description: str
    model: str | None = None
    model_kwargs: dict[str, Any] = Field(default_factory=dict, repr=False)
    tools: list[str] = Field(default_factory=list)
    system_prompt: str | None = None


def _expand(value: Any) -> Any:
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in os.environ:
                raise ConfigurationError(f"Missing environment variable {name}")
            return os.environ[name]

        return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", replace, value)
    if isinstance(value, list):
        return [_expand(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand(item) for key, item in value.items()}
    return value


class ProfileOptions(StrictOptions):
    harness: HarnessName
    model: str = Field(min_length=1)
    model_kwargs: dict[str, Any] = Field(default_factory=dict, repr=False)
    system_prompt: str | None = None
    # None keeps defaults; [] disables tools; a list is an explicit selection.
    tools: list[str] | None = None
    mcp_servers: dict[str, dict[str, Any]] = Field(default_factory=dict, repr=False)
    subagents: dict[str, SubagentOptions] = Field(default_factory=dict)
    features: FeatureOptions = Field(default_factory=FeatureOptions)
    temporal: TemporalOptions | None = None
    recovery: RecoveryOptions | None = None
    harness_options: dict[str, Any] = Field(default_factory=dict, repr=False)
    max_turns: int | None = Field(default=None, ge=1, le=1000)

    @field_validator("tools")
    @classmethod
    def unique_tools(cls, names: list[str] | None) -> list[str] | None:
        if names is None:
            return None
        if any(not name.strip() for name in names) or len(set(names)) != len(names):
            raise ValueError("Tool names must be nonempty and unique")
        return names

    @field_validator("model_kwargs")
    @classmethod
    def validate_model_kwargs(cls, values: dict[str, Any]) -> dict[str, Any]:
        reserved = {"model", "messages", "tools", "stream", "tool_choice"} & values.keys()
        if reserved:
            raise ValueError(f"These fields are managed by the harness: {sorted(reserved)}")
        return values

    @field_validator("mcp_servers")
    @classmethod
    def validate_mcp_servers(cls, servers: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        from .mcp_config import normalize_servers

        servers = normalize_servers(servers)
        for name, config in servers.items():
            if not name or ("url" in config) == ("command" in config):
                raise ValueError("Each named MCP server needs exactly one of url or command")
            for key in ("url", "command"):
                if key in config and (not isinstance(config[key], str) or not config[key].strip()):
                    raise ValueError(f"MCP {key} must be a nonempty string")
            if "args" in config and (
                not isinstance(config["args"], list)
                or not all(isinstance(arg, str) for arg in config["args"])
            ):
                raise ValueError("MCP args must be a list of strings")
        return servers

    @classmethod
    def from_yaml(cls, path: str | Path) -> ProfileOptions:
        import yaml

        return cls.model_validate(_expand(yaml.safe_load(Path(path).read_text())))

    @classmethod
    def from_json(cls, path: str | Path) -> ProfileOptions:
        return cls.model_validate(_expand(json.loads(Path(path).read_text())))

    def identity(self) -> str:
        """Only this opaque ID, never the resolved profile, enters workflow history."""
        if self.temporal and self.temporal.profile_id:
            return self.temporal.profile_id
        data = self.model_dump(exclude={"temporal"})
        try:
            encoded = json.dumps(data, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(
                "Profiles containing native objects need temporal.profile_id with a stable version"
            ) from exc
        return hashlib.sha256(encoded.encode()).hexdigest()
