from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

from ..errors import ConfigurationError
from ..harnesses.base import create_adapter
from ..profiles import ProfileOptions
from ..runs import RunResult
from ..runtime.serialization import dump_result
from ..tools import Tool
from ..types import AssistantMessage, UserMessage
from .client import connect, queue_name
from .workflows import AgentWorkflow


class LiteAgentWorker:
    """Single-host DeepAgents worker with a persisted graph checkpoint file.

    A file lock prevents two workers on this host from racing the same SQLite
    checkpoint store. Shared/distributed checkpoint storage is a later deployment
    feature, not an implicit guarantee of this local worker.
    """

    def __init__(
        self, *, profile: ProfileOptions, tools: list[Tool] | None = None, cwd: str | Path = "."
    ):
        if profile.temporal is None:
            raise ConfigurationError("LiteAgentWorker requires profile.temporal")
        self.profile = profile
        self.profile_id = profile.identity()
        self.tools = tools or []
        self.cwd = Path(cwd).resolve()
        if not self.cwd.is_dir():
            raise ConfigurationError(f"Working directory does not exist: {self.cwd}")
        create_adapter(profile, cwd=self.cwd, tools=self.tools, session_id="validate")
        path = Path(profile.temporal.checkpoint_path)
        self.checkpoint_path = path if path.is_absolute() else self.cwd / path

    async def _heartbeat(self) -> None:
        assert self.profile.temporal is not None
        while True:
            activity.heartbeat()
            await asyncio.sleep(min(2, self.profile.temporal.heartbeat_timeout_seconds / 3))

    @activity.defn(name="liteagents.run")
    async def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        if request["profile_id"] != self.profile_id:
            raise ApplicationError(
                "This worker does not have the requested profile version", non_retryable=True
            )
        info = activity.info()
        assert info.workflow_id is not None and info.workflow_run_id is not None
        adapter = create_adapter(
            self.profile,
            cwd=self.cwd,
            tools=self.tools,
            session_id=f"{info.workflow_id}:{info.workflow_run_id}",
        )
        beating = asyncio.create_task(self._heartbeat())
        try:
            await adapter.open()
            messages = [
                event
                async for event in adapter.query(
                    request["prompt"], run_id=info.workflow_id, resume=True
                )
                if isinstance(event, (AssistantMessage, UserMessage))
            ]
            result = dump_result(
                RunResult(
                    info.workflow_id, self.profile.harness, messages, adapter.native_session_id
                )
            )
            if len(json.dumps(result).encode()) > 1_500_000:
                raise ConfigurationError(
                    "Run result is too large for Temporal; store large tool outputs as external artifacts"
                )
            return result
        except ConfigurationError as exc:
            raise ApplicationError(str(exc), type="ConfigurationError", non_retryable=True) from exc
        finally:
            beating.cancel()
            with suppress(asyncio.CancelledError):
                await beating
            await adapter.close()

    @asynccontextmanager
    async def running(self):
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(self.checkpoint_path) + ".worker.lock")
        try:
            lock.acquire(timeout=0)
        except Timeout as exc:
            raise ConfigurationError(
                "Another worker already owns this local checkpoint store"
            ) from exc
        try:
            client = await connect(self.profile)
            # The package imports optional/legacy libraries outside the workflow
            # sandbox. AgentWorkflow itself only issues deterministic SDK commands.
            runner = SandboxedWorkflowRunner(
                restrictions=SandboxRestrictions.default.with_passthrough_modules("liteagents")
            )
            async with Worker(
                client,
                task_queue=queue_name(self.profile),
                workflows=[AgentWorkflow],
                activities=[self.execute],
                workflow_runner=runner,
                max_concurrent_activities=1,
            ) as worker:
                yield worker
        finally:
            lock.release()

    async def run(self) -> None:
        async with self.running():
            await asyncio.Event().wait()
