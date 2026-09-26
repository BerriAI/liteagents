from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from filelock import FileLock, Timeout
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

from ..errors import ConfigurationError
from ..function_tools import ToolInput, adapt_tools
from ..harnesses.base import create_adapter
from ..profiles import ProfileOptions
from ..runs import RunResult
from ..runtime.control import CURRENT, RunControl, operation_failed, retryable
from ..runtime.conversation import load_history
from ..runtime.events import payload
from ..runtime.fallback import fallback_profile
from ..runtime.serialization import dump_result
from ..storage.ownership import own_run
from ..storage.store import digest
from ..types import AssistantMessage, UserMessage
from .client import connect, queue_name
from .state import run_key, run_store
from .workflows import AgentWorkflow


class LiteAgentWorker:
    """Execute native harnesses with durable operations and exclusive run ownership."""

    def __init__(
        self, *, profile: ProfileOptions, tools: list[ToolInput] | None = None, cwd: str | Path = "."
    ):
        if profile.temporal is None:
            raise ConfigurationError("LiteAgentWorker requires profile.temporal")
        self.profile, self.profile_id = profile, profile.identity()
        self.tools, self.cwd = adapt_tools(tools or []), Path(cwd).resolve()
        if not self.cwd.is_dir():
            raise ConfigurationError(f"Working directory does not exist: {self.cwd}")
        create_adapter(profile, cwd=self.cwd, tools=self.tools, session_id="validate")
        self.store = run_store(profile, self.cwd)
        if (
            self.store.db.postgres
            and profile.harness == "deepagents"
            and not profile.temporal.checkpoint_url
        ):
            raise ConfigurationError(
                "Shared DeepAgents workers need temporal.checkpoint_url for PostgreSQL graph storage"
            )

    async def _heartbeat(self, control: RunControl) -> None:
        assert self.profile.temporal is not None
        while True:
            await control.check()
            activity.heartbeat()
            await asyncio.sleep(min(1, self.profile.temporal.heartbeat_timeout_seconds / 3))

    async def _run(self, request, control, session_id):
        history = []
        if request.get("history_key"):
            snapshot = await control.get(request["history_key"])
            if snapshot is None:
                raise ConfigurationError("Conversation snapshot is unavailable")
            history = load_history(snapshot)
        fallbacks = self.profile.recovery.harness_fallbacks if self.profile.recovery else []
        choices = [self.profile.harness, *fallbacks]
        selected = await control.get("active_harness") or self.profile.harness
        token = CURRENT.set(control)
        try:
            for index in range(choices.index(selected), len(choices)):
                profile = (
                    self.profile if index == 0 else fallback_profile(self.profile, choices[index])
                )
                control.profile = profile
                adapter = create_adapter(
                    profile,
                    cwd=self.cwd,
                    tools=self.tools,
                    session_id=session_id + ":" + profile.harness,
                    history=history,
                )
                messages = []
                occurrences: dict[str, int] = {}
                try:
                    await adapter.open()
                    async for event in adapter.query(
                        request["prompt"], run_id=request["run_id"], resume=True
                    ):
                        wire = payload(event)
                        if isinstance(event, (AssistantMessage, UserMessage)):
                            messages.append(event)
                            fingerprint = digest(wire)
                            occurrences[fingerprint] = occurrences.get(fingerprint, 0) + 1
                            event_key = f"message:{fingerprint}:{occurrences[fingerprint]}"
                        else:
                            event_key = uuid4().hex
                        await self.store.event(control.run_key, event_key, wire)
                    return dump_result(
                        RunResult(
                            request["run_id"], profile.harness, messages, adapter.native_session_id
                        )
                    )
                except Exception as exc:
                    if control.tool_started or index == len(choices) - 1 or not retryable(exc):
                        raise
                    await control.put("active_harness", choices[index + 1])
                    await control.emit("harness_fallback", harness=choices[index + 1])
                finally:
                    await adapter.close()
            raise AssertionError("unreachable")
        finally:
            CURRENT.reset(token)

    @activity.defn(name="liteagents.run")
    async def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        if request["profile_id"] != self.profile_id:
            raise ApplicationError(
                "This worker does not have the requested profile version", non_retryable=True
            )
        info = activity.info()
        assert info.workflow_id is not None and info.workflow_run_id is not None
        request = {**request, "run_id": info.workflow_id}
        key = run_key(self.profile, info.workflow_id)
        state = await self.store.ensure_run(key, self.profile_id)
        await self.store.put(key, "workflow_id", info.workflow_id)
        if state["result"] is not None:
            return {"state_key": key}
        async with own_run(self.store, key) as check_owner:
            control = RunControl(
                self.profile, run_key=key, store=self.store, check_owner=check_owner
            )
            control.tool_started = state["tool_started"]
            await self.store.set_status(key, "running")
            running = asyncio.create_task(
                self._run(request, control, f"{info.workflow_id}:{info.workflow_run_id}")
            )
            beating = asyncio.create_task(self._heartbeat(control))
            try:
                done, _ = await asyncio.wait(
                    [running, beating], return_when=asyncio.FIRST_COMPLETED
                )
                if beating in done:
                    await beating
                result = await running
                await control.check()
                await self.store.set_status(key, "completed", result=result)
                return {"state_key": key}
            except asyncio.CancelledError:
                current = await self.store.get_run(key)
                if current["cancelled"]:
                    await self.store.set_status(key, "cancelled")
                raise
            except Exception as exc:
                permanent = operation_failed(exc) or not retryable(exc)
                await self.store.set_status(
                    key, "failed" if permanent else "running", error=type(exc).__name__
                )
                raise ApplicationError(
                    str(exc), type=type(exc).__name__, non_retryable=permanent
                ) from exc
            finally:
                running.cancel()
                beating.cancel()
                await asyncio.gather(running, beating, return_exceptions=True)

    @asynccontextmanager
    async def running(self):
        await self.store.setup()
        # Local SQLite deployment owns its checkpoint files for the full worker
        # lifetime. PostgreSQL workers share storage and lock individual runs.
        lock = None
        if not self.store.db.postgres:
            lock = FileLock(str(self.store.db.path) + ".worker.lock")
            try:
                lock.acquire(timeout=0)
            except Timeout as exc:
                raise ConfigurationError(
                    "Another worker already owns this local checkpoint store"
                ) from exc
        try:
            client = await connect(self.profile)
            runner = SandboxedWorkflowRunner(
                restrictions=SandboxRestrictions.default.with_passthrough_modules("liteagents")
            )
            assert self.profile.temporal is not None
            async with Worker(
                client,
                task_queue=queue_name(self.profile),
                workflows=[AgentWorkflow],
                activities=[self.execute],
                workflow_runner=runner,
                max_concurrent_activities=self.profile.temporal.max_concurrent_runs,
            ) as worker:
                yield worker
        finally:
            if lock:
                lock.release()

    async def purge(self, *, older_than_days: int | None = None) -> int:
        """Remove terminal run data; active runs and Temporal history are untouched."""
        assert self.profile.temporal is not None
        await self.store.setup()
        days = self.profile.temporal.retention_days if older_than_days is None else older_than_days
        from .retention import purge

        return await purge(self, days)

    async def run(self) -> None:
        async with self.running():
            await asyncio.Event().wait()
