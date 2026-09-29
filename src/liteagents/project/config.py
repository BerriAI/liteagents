"""agent.toml: the four choices a generated project is built from."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast, get_args

from ..errors import ConfigurationError
from ..profiles import HarnessName

Channel = Literal["slack", "terminal"]
CHANNELS: tuple[str, ...] = get_args(Channel)
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}")


def name_error(name: str) -> str | None:
    if not _NAME.fullmatch(name):
        return "Use letters, digits, - and _ (start with a letter or digit, at most 64 characters)"
    return None


def model_error(model: str) -> str | None:
    if not _MODEL.fullmatch(model):
        return "Enter a model ID such as anthropic/claude-sonnet-5-5"
    return None


@dataclass(frozen=True, slots=True)
class AgentConfig:
    name: str
    harness: HarnessName
    model: str
    channel: Channel

    def __post_init__(self) -> None:
        error = name_error(self.name) or model_error(self.model)
        if error:
            raise ConfigurationError(error)
        if self.harness not in get_args(HarnessName):
            raise ConfigurationError(
                f"Unknown harness {self.harness!r}; choose one of {', '.join(get_args(HarnessName))}"
            )
        if self.channel not in CHANNELS:
            raise ConfigurationError(
                f"Unknown channel {self.channel!r}; choose one of {', '.join(CHANNELS)}"
            )

    @classmethod
    def load(cls, path: str | Path) -> AgentConfig:
        try:
            data = tomllib.loads(Path(path).read_text())
        except FileNotFoundError as exc:
            raise ConfigurationError(f"No agent.toml at {path}") from exc
        missing = [key for key in ("name", "harness", "model", "channel") if key not in data]
        if missing:
            raise ConfigurationError(f"agent.toml is missing {', '.join(missing)}")
        return cls(
            name=str(data["name"]),
            harness=cast(HarnessName, data["harness"]),
            model=str(data["model"]),
            channel=cast(Channel, data["channel"]),
        )

    def to_toml(self) -> str:
        return "".join(
            f"{key} = {json.dumps(getattr(self, key))}\n"
            for key in ("name", "harness", "model", "channel")
        )
