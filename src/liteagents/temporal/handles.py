from __future__ import annotations

import asyncio
from typing import Any

from ..errors import ConfigurationError, RunNotFoundError
from ..runs import RunResult
from ..runtime.events import envelope
from ..runtime.serialization import load_result
from ..storage import RunStore


class TemporalRun:
    def __init__(self, handle: Any, store: RunStore, key: str, profile_id: str):
        self.handle, self.store, self.key, self.profile_id = handle, store, key, profile_id
        self.run_id = handle.id

    async def state(self):
        state = await self.store.get_run(self.key)
        if state["profile_id"] != self.profile_id:
            raise ConfigurationError("Run belongs to a different profile version")
        return state

    async def result(self) -> RunResult:
        reference = await self.handle.result()
        if "state_key" not in reference:  # Previously submitted milestone 2 runs.
            return load_result(reference)
        state = await self.state()
        if state["result"] is None:
            raise RunNotFoundError("Run result is unavailable or has expired")
        return load_result(state["result"])

    async def status(self) -> str:
        description = await self.handle.describe()
        status = description.status.name.lower()
        if status == "running":
            try:
                return (await self.state())["status"]
            except RunNotFoundError:
                return status
        state = await self.state()
        if status in ("completed", "failed", "canceled", "terminated", "timed_out"):
            stored = (
                "cancelled"
                if status == "canceled"
                else ("failed" if status in ("terminated", "timed_out") else status)
            )
            if state["status"] != stored:
                await self.store.set_status(self.key, stored)
            return stored
        return status

    async def events(self, after: int = 0):
        while True:
            await self.state()
            rows = await self.store.events(self.key, after)
            for row in rows:
                after = row["cursor"]
                yield envelope(row)
            if rows:
                continue
            if await self.status() not in ("running", "waiting_for_approval"):
                # Completion can race the previous read; drain committed events.
                for row in await self.store.events(self.key, after):
                    yield envelope(row)
                return
            await asyncio.sleep(0.1)

    async def approvals(self):
        await self.state()
        return await self.store.pending_approvals(self.key)

    async def approve(self, approval_id: str, *, allow: bool = True):
        await self.state()
        await self.store.approve(self.key, approval_id, allow)

    async def cancel(self):
        await self.state()
        await self.store.cancel(self.key)
        await self.handle.cancel()
