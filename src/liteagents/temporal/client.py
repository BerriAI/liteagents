from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from ..errors import (
    ConfigurationError,
    RunAlreadyExistsError,
    RunNotFoundError,
    UnsupportedFeatureError,
)
from ..harnesses import get_capabilities
from ..profiles import ProfileOptions
from ..runs import RunResult
from ..runtime.serialization import load_result
from ..tools import Tool
from ..types import AgentEvent, Message


async def connect(profile: ProfileOptions) -> Client:
    options = profile.temporal
    assert options is not None
    return await Client.connect(
        options.address,
        namespace=options.namespace,
        api_key=options.api_key,
        tls=options.tls or bool(options.api_key),
    )


def queue_name(profile: ProfileOptions) -> str:
    """A worker must never consume a different profile's tasks on a shared queue."""
    import hashlib

    assert profile.temporal is not None
    suffix = hashlib.sha256(profile.identity().encode()).hexdigest()[:16]
    return f"{profile.temporal.task_queue}-{suffix}"


class TemporalRun:
    def __init__(self, handle: Any):
        self.handle = handle
        self.run_id = handle.id

    async def result(self) -> RunResult:
        return load_result(await self.handle.result())

    async def status(self) -> str:
        description = await self.handle.describe()
        return description.status.name.lower()


class TemporalRuntime:
    def __init__(
        self, profile: ProfileOptions, *, cwd: Path, tools: list[Tool], session_id: str | None
    ):
        if not get_capabilities(profile.harness).temporal:
            raise UnsupportedFeatureError(
                f"Temporal recovery is currently supported for deepagents, not {profile.harness}"
            )
        if (
            profile.features.streaming
            or profile.features.subagents
            or profile.subagents
            or profile.recovery
        ):
            raise UnsupportedFeatureError(
                "Temporal live streaming, subagents, and operation fallbacks are milestone 3 features"
            )
        if tools:
            raise ConfigurationError(
                "Register temporal tools on LiteAgentWorker(tools=...), not the application client"
            )
        if session_id:
            raise UnsupportedFeatureError(
                "Each durable query is an independent run; attach with get_run(run_id)"
            )
        self.profile = profile
        self.profile_id = profile.identity()
        self.history: list[Message] = []
        self.client: Client | None = None

    async def open(self) -> None:
        self.client = await connect(self.profile)

    async def close(self) -> None:
        # No workflow cancellation: the worker owns execution after submission.
        self.client = None

    async def start_run(self, prompt: str, *, run_id: str | None = None) -> TemporalRun:
        if self.client is None:
            raise RuntimeError("Use LiteAgentClient as an async context manager")
        run_id = uuid4().hex if run_id is None else run_id
        if not run_id.strip():
            raise ConfigurationError("run_id must be nonempty")
        options = self.profile.temporal
        assert options is not None
        request = {
            "prompt": prompt,
            "profile_id": self.profile_id,
            "activity_timeout": options.activity_timeout_seconds,
            "heartbeat_timeout": options.heartbeat_timeout_seconds,
            "attempts": options.worker_recovery_attempts,
        }
        try:
            handle = await self.client.start_workflow(
                "LiteAgentsRun",
                request,
                id=run_id,
                task_queue=queue_name(self.profile),
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            )
        except WorkflowAlreadyStartedError as exc:
            raise RunAlreadyExistsError(f"Run {run_id!r} already exists") from exc
        return TemporalRun(handle)

    async def get_run(self, run_id: str) -> TemporalRun:
        if self.client is None:
            raise RuntimeError("Use LiteAgentClient as an async context manager")
        handle = self.client.get_workflow_handle(run_id)
        try:
            await handle.describe()
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND:
                raise RunNotFoundError(f"No Temporal run {run_id!r}") from exc
            raise
        return TemporalRun(handle)

    async def query(self, prompt: str, *, run_id: str | None = None) -> AsyncIterator[AgentEvent]:
        run = await self.start_run(prompt, run_id=run_id)
        result = await run.result()
        self.history.extend(result.messages)
        for message in result.messages:
            yield message
