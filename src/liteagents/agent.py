"""The profile-driven public SDK. Every selected harness owns its native loop."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

from .function_tools import ToolInput, adapt_tools
from .profiles import ProfileOptions
from .runs import RunResult
from .types import AgentEvent, Message


@dataclass
class LiteAgentOptions:
    profile: ProfileOptions
    cwd: str | Path = "."
    tools: list[ToolInput] = field(default_factory=list)
    session_id: str | None = None

    def __post_init__(self):
        if not isinstance(self.profile, ProfileOptions):
            raise TypeError("profile must be a ProfileOptions instance")
        self.tools = adapt_tools(self.tools)


class LiteAgentClient:
    def __init__(
        self, *, profile: ProfileOptions | None = None, cwd: str | Path | None = None,
        tools: list[ToolInput] | None = None, session_id: str | None = None,
        options: LiteAgentOptions | None = None,
    ):
        if options is not None:
            if any(value is not None for value in (profile, cwd, tools, session_id)):
                raise TypeError("Pass profile/cwd/tools/session_id or options, not both")
        else:
            if profile is None:
                raise TypeError("LiteAgentClient requires profile=ProfileOptions(...)")
            options = LiteAgentOptions(
                profile=profile, cwd="." if cwd is None else cwd,
                tools=[] if tools is None else tools, session_id=session_id,
            )
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
        """Continue this client's conversation, in direct or durable execution."""
        async with aclosing(self._runtime.query(prompt, run_id=run_id)) as events:
            async for event in events:
                yield event

    @property
    def capabilities(self):
        """Capabilities for this profile, including its registered application tools."""
        from .harnesses import get_capabilities

        return get_capabilities(self._options.profile, tools=self._options.tools)

    @property
    def history(self) -> list[Message]:
        return list(self._runtime.history)

    async def get_run(self, run_id: str) -> Any:
        """Attach to an existing run without submitting a new query."""
        return await self._runtime.get_run(run_id)

    async def start_run(self, prompt: str, *, run_id: str | None = None) -> Any:
        """Start an independent job. Temporal jobs outlive this client; local jobs do not."""
        return await self._runtime.start_run(prompt, run_id=run_id)


async def query(
    *, prompt: str, profile: ProfileOptions | None = None, cwd: str | Path | None = None,
    tools: list[ToolInput] | None = None, session_id: str | None = None,
    options: LiteAgentOptions | None = None, run_id: str | None = None,
) -> AsyncGenerator[AgentEvent, None]:
    async with (
        LiteAgentClient(
            profile=profile, cwd=cwd, tools=tools, session_id=session_id, options=options,
        ) as client,
        aclosing(client.query(prompt, run_id=run_id)) as events,
    ):
        async for event in events:
            yield event


async def run(
    prompt: str,
    *,
    profile: ProfileOptions,
    cwd: str | Path = ".",
    tools: list[ToolInput] | None = None,
    run_id: str | None = None,
) -> RunResult:
    """Run an independent task and return its final result.

    Each call uses a fresh client and closes it before returning. Local work is
    cancelled if this call is cancelled; Temporal work continues on its worker.
    For conversation history, streaming, approvals, or explicit cancellation of
    durable work, use LiteAgentClient and its query/run-handle methods.
    """
    async with LiteAgentClient(profile=profile, cwd=cwd, tools=tools) as client:
        handle = await client.start_run(prompt, run_id=run_id)
        return await handle.result()
