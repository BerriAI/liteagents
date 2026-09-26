from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing
from pathlib import Path
from uuid import uuid4

from ..errors import ConfigurationError, RunAlreadyExistsError, RunNotFoundError
from ..harnesses.base import create_adapter
from ..profiles import ProfileOptions
from ..runs import LocalRun, RunResult
from ..tools import Tool
from ..types import AgentEvent, AssistantMessage, Message, UserMessage
from .control import CURRENT, RunControl, retryable
from .events import payload
from .fallback import fallback_profile


class DirectRuntime:
    def __init__(
        self, profile: ProfileOptions, *, cwd: Path, tools: list[Tool], session_id: str | None
    ):
        if not cwd.is_dir():
            raise ConfigurationError(f"Working directory does not exist: {cwd}")
        self.profile, self.cwd, self.tools = profile, cwd, tools
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
        self.owner: asyncio.Task | None = None
        self.queue: asyncio.Queue = asyncio.Queue()
        self._closing = False
        self.jobs: set[DirectRuntime] = set()
        self.job_tasks: set[asyncio.Task] = set()
        self._close_task: asyncio.Task | None = None

    async def open(self):
        self.ready = asyncio.get_running_loop().create_future()
        self.owner = asyncio.create_task(self._serve())
        try:
            await self.ready
        except BaseException:
            await asyncio.gather(self.owner, return_exceptions=True)
            raise

    async def _serve(self):
        # MCP transports use task-local cancellation scopes. Open, query,
        # fallback and close every adapter in this same owner task.
        try:
            await self.adapter.open()
            self._open = True
            self.ready.set_result(None)
            while True:
                prompt, run = await self.queue.get()
                run.task = self.owner
                try:
                    await run.control.check()
                    async for _ in self._query(prompt, run):
                        pass
                except asyncio.CancelledError as exc:
                    run.fail(exc)
                    if self._closing:
                        raise
                    assert self.owner is not None
                    self.owner.uncancel()
                except Exception as exc:  # noqa: BLE001 - delivered by run.result()
                    run.fail(exc)
                finally:
                    run.task = None
                    self._lock.release()
                    run._settled.set()
        except BaseException as exc:
            if not self.ready.done():
                self.ready.set_exception(exc)
            raise
        finally:
            self._open = False
            while not self.queue.empty():
                _, run = self.queue.get_nowait()
                run.fail(asyncio.CancelledError())
                run._settled.set()
                if self._lock.locked():
                    self._lock.release()
            await self.adapter.close()

    async def close(self):
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    async def _close(self):
        self._closing = True
        self._open = False
        await asyncio.gather(*(job.close() for job in list(self.jobs)))
        await asyncio.gather(*list(self.job_tasks))
        if self.owner is not None:
            self.owner.cancel()
            results = await asyncio.gather(self.owner, return_exceptions=True)
            if isinstance(results[0], Exception):
                raise results[0]

    async def get_run(self, run_id: str) -> LocalRun:
        if run_id not in self.runs:
            raise RunNotFoundError(f"No local run {run_id!r}; local handles belong to this client")
        return self.runs[run_id]

    def prepare(self, run_id: str | None, *, conversation=True) -> LocalRun:
        if not self._open:
            raise RuntimeError("Use LiteAgentClient as an async context manager")
        if conversation and self._lock.locked():
            raise ConfigurationError("Concurrent queries on one conversation are not supported")
        run_id = uuid4().hex if run_id is None else run_id
        if not run_id.strip():
            raise ConfigurationError("run_id must be nonempty")
        if run_id in self.runs:
            raise RunAlreadyExistsError(f"Run {run_id!r} already exists")
        run = LocalRun(run_id)
        run.control = RunControl(self.profile, run_key=run_id, emit=run.emit)
        self.runs[run_id] = run
        return run

    async def start_run(self, prompt: str, *, run_id: str | None = None) -> LocalRun:
        reserved = self.prepare(run_id, conversation=False)
        profile = self.profile
        if profile.harness.startswith("opencode"):
            options = dict(profile.harness_options)
            base = Path(profile.native_options().get("state_dir", self.cwd / ".liteagents" / "jobs"))
            options[profile.harness] = {
                **options.get(profile.harness, {}), "state_dir": str(base / uuid4().hex),
            }
            profile = profile.model_copy(update={"harness_options": options})
        job = DirectRuntime(profile, cwd=self.cwd, tools=self.tools, session_id=None)
        self.jobs.add(job)
        try:
            await job.open()
            job.runs[reserved.run_id] = reserved
            reserved.control = RunControl(profile, run_key=reserved.run_id, emit=reserved.emit)
            await job._enqueue(prompt, reserved)
        except BaseException as exc:
            self.jobs.discard(job)
            reserved.fail(exc)
            reserved._settled.set()
            await job.close()
            raise
        run = reserved

        async def finish():
            try:
                await run._settled.wait()
            finally:
                await job.close()
                self.jobs.discard(job)

        task = asyncio.create_task(finish())
        self.job_tasks.add(task)
        return run

    async def _submit(self, prompt, *, run_id):
        run = self.prepare(run_id)
        await self._enqueue(prompt, run)
        return run

    async def _enqueue(self, prompt, run):
        await self._lock.acquire()
        self.queue.put_nowait((prompt, run))

    async def query(self, prompt: str, *, run_id: str | None = None) -> AsyncIterator[AgentEvent]:
        run = await self._submit(prompt, run_id=run_id)
        try:
            async for event in run.events():
                if event.message is not None:
                    yield event.message
            await run.result()
        finally:
            if not run._done.is_set():
                await run.cancel()
            await run._settled.wait()

    async def _query(self, prompt: str, run: LocalRun) -> AsyncGenerator[AgentEvent, None]:
        assert run.control is not None
        token = CURRENT.set(run.control)
        messages: list[Message] = []
        fallbacks = self.profile.recovery.harness_fallbacks if self.profile.recovery else []
        try:
            for index, harness in enumerate([self.profile.harness, *fallbacks]):
                run.control.profile = self.adapter.profile
                try:
                    async with aclosing(self.adapter.query(prompt, run_id=run.run_id)) as stream:
                        async for event in stream:
                            if isinstance(event, (AssistantMessage, UserMessage)):
                                messages.append(event)
                            await run.emit(payload(event))
                            yield event
                    break
                except Exception as exc:
                    if run.control.tool_started or index == len(fallbacks) or not retryable(exc):
                        raise
                    await self.adapter.close()
                    next_name = fallbacks[index]
                    profile = fallback_profile(self.profile, next_name)
                    self.adapter = create_adapter(
                        profile, cwd=self.cwd, tools=self.tools, session_id=uuid4().hex
                    )
                    await self.adapter.open()
                    await run.emit({"kind": "harness_fallback", "harness": next_name})
                    messages.clear()
            self.history.extend([UserMessage(prompt), *messages])
            run.finish(
                RunResult(
                    run.run_id,
                    self.adapter.profile.harness,
                    messages,
                    self.adapter.native_session_id or self.adapter.session_id,
                )
            )
        except BaseException as exc:
            run.fail(exc)
            raise
        finally:
            CURRENT.reset(token)
