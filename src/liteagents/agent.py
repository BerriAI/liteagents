"""The profile-driven public SDK. Every selected harness owns its native loop."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

from .profiles import ProfileOptions
from .tools import Tool
from .types import AgentEvent, Message


@dataclass
class LiteAgentOptions:
    profile: ProfileOptions
    cwd: str | Path = "."
    tools: list[Tool] = field(default_factory=list)
    session_id: str | None = None

    def __post_init__(self):
        if not isinstance(self.profile, ProfileOptions):
            raise TypeError("profile must be a ProfileOptions instance")


class LiteAgentClient:
    def __init__(self, *, options: LiteAgentOptions):
        self._options = options
        if options.profile.temporal:
            try:
                from .temporal.client import TemporalRuntime
            except ModuleNotFoundError as exc:
                from .errors import MissingDependencyError

                raise MissingDependencyError(
                    "Install liteagents[temporal] for durable runs"
                ) from exc
            runtime: Any = TemporalRuntime
        else:
            from .runtime.direct import DirectRuntime

            runtime = DirectRuntime
        self._runtime = runtime(
            options.profile,
            cwd=Path(options.cwd).resolve(),
            tools=options.tools,
            session_id=options.session_id,
        )

    async def __aenter__(self) -> Self:
        await self._runtime.open()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._runtime.close()

    async def query(
        self, prompt: str, *, run_id: str | None = None
    ) -> AsyncGenerator[AgentEvent, None]:
        async with aclosing(self._runtime.query(prompt, run_id=run_id)) as events:
            async for event in events:
                yield event

    @property
    def history(self) -> list[Message]:
        return list(self._runtime.history)

    async def get_run(self, run_id: str) -> Any:
        """Attach to an existing run without submitting a new query."""
        return await self._runtime.get_run(run_id)

    async def start_run(self, prompt: str, *, run_id: str | None = None) -> Any:
        """Start without waiting. Temporal runs outlive this client; local runs do not."""
        return await self._runtime.start_run(prompt, run_id=run_id)


async def query(
    *, prompt: str, options: LiteAgentOptions, run_id: str | None = None
) -> AsyncGenerator[AgentEvent, None]:
    async with (
        LiteAgentClient(options=options) as client,
        aclosing(client.query(prompt, run_id=run_id)) as events,
    ):
        async for event in events:
            yield event
