from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import aclosing
from pathlib import Path
from uuid import uuid4

from ..errors import ConfigurationError, RunAlreadyExistsError, RunNotFoundError
from ..harnesses.base import create_adapter
from ..profiles import ProfileOptions
from ..runs import LocalRun, RunResult
from ..tools import Tool
from ..types import AgentEvent, AssistantMessage, Message, UserMessage


class DirectRuntime:
    def __init__(
        self, profile: ProfileOptions, *, cwd: Path, tools: list[Tool], session_id: str | None
    ):
        if not cwd.is_dir():
            raise ConfigurationError(f"Working directory does not exist: {cwd}")
        self.profile = profile
        self.adapter = create_adapter(
            profile,
            cwd=cwd,
            tools=tools,
            session_id=session_id or uuid4().hex,
            resume_session=session_id is not None,
        )
        self.history: list[Message] = []
        self.runs: dict[str, LocalRun] = {}
        self._lock = asyncio.Lock()
        self._open = False

    async def open(self) -> None:
        try:
            await self.adapter.open()
            self._open = True
        except BaseException:
            await self.adapter.close()
            raise

    async def close(self) -> None:
        self._open = False
        await self.adapter.close()

    async def get_run(self, run_id: str) -> LocalRun:
        if run_id not in self.runs:
            raise RunNotFoundError(f"No local run {run_id!r}; local handles belong to this client")
        return self.runs[run_id]

    async def query(self, prompt: str, *, run_id: str | None = None) -> AsyncIterator[AgentEvent]:
        if not self._open:
            raise RuntimeError("Use LiteAgentClient as an async context manager")
        if self._lock.locked():
            raise ConfigurationError("Concurrent queries on one conversation are not supported")
        async with self._lock:
            run_id = uuid4().hex if run_id is None else run_id
            if not run_id.strip():
                raise ConfigurationError("run_id must be nonempty")
            if run_id in self.runs:
                raise RunAlreadyExistsError(f"Run {run_id!r} already exists")
            run = LocalRun(run_id)
            self.runs[run_id] = run
            messages: list[Message] = []
            self.history.append(UserMessage(prompt))
            try:
                async with aclosing(self.adapter.query(prompt, run_id=run_id)) as events:
                    async for event in events:
                        if isinstance(event, (AssistantMessage, UserMessage)):
                            messages.append(event)
                            self.history.append(event)
                        yield event
                run.finish(
                    RunResult(
                        run_id,
                        self.profile.harness,
                        messages,
                        self.adapter.native_session_id or self.adapter.session_id,
                    )
                )
            except BaseException as exc:
                run.fail(exc)
                raise
