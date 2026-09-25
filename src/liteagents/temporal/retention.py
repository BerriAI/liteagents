"""Delete expired SDK run data and its owned checkpoints under run ownership."""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from ..errors import ConfigurationError
from ..storage.ownership import own_run


async def purge(worker, days: int) -> int:
    if days < 0:
        raise ConfigurationError("Retention duration must be nonnegative")
    store = worker.store
    await store.setup()
    # A worker cannot update SQL after a hard kill or workflow termination.
    # Reconcile aged nonterminal rows before deciding which data may be removed.
    stale = await store.db.execute(
        "SELECT r.run_key,v.value FROM la_runs r JOIN la_values v ON r.run_key=v.run_key AND v.name='workflow_id' WHERE r.profile_id=? AND r.status IN ('running','waiting_for_approval') AND r.updated<? LIMIT 1000",
        worker.profile_id,
        time.time() - days * 86400,
    )
    if stale:
        from temporalio.service import RPCError, RPCStatusCode

        from .client import connect
        from .handles import TemporalRun

        client = await connect(worker.profile)
        for key, wire in stale:
            handle = client.get_workflow_handle(json.loads(wire))
            run = TemporalRun(handle, store, key, worker.profile_id)
            try:
                await run.status()
            except RPCError as exc:
                if exc.status != RPCStatusCode.NOT_FOUND:
                    raise
                # Temporal's namespace retention can expire before SDK retention.
                await store.set_status(key, "failed", error="Workflow history expired")
    keys = await store.db.execute(
        "SELECT run_key FROM la_runs WHERE profile_id=? AND status IN ('completed','failed','cancelled') AND updated<? LIMIT 1000",
        worker.profile_id,
        time.time() - days * 86400,
    )
    removed = 0
    async with AsyncExitStack() as stack:
        saver: Any = None
        for (key,) in keys:
            async with own_run(store, key):
                rows = await store.db.execute(
                    "SELECT value FROM la_values WHERE run_key=? AND name LIKE 'checkpoint:%'", key
                )
                for (wire,) in rows:
                    record = json.loads(wire)
                    if record["kind"] == "deepagents":
                        if saver is None:
                            options = worker.profile.temporal
                            if options.checkpoint_url:
                                from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

                                saver = await stack.enter_async_context(
                                    AsyncPostgresSaver.from_conn_string(options.checkpoint_url)
                                )
                            else:
                                from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

                                path = Path(options.checkpoint_path)
                                if not path.is_absolute():
                                    path = worker.cwd / path
                                saver = await stack.enter_async_context(
                                    AsyncSqliteSaver.from_conn_string(str(path))
                                )
                        await saver.adelete_thread(record["session"])
                    elif record["kind"] == "native":
                        path = Path(record["path"]).resolve()
                        if not path.is_relative_to(worker.cwd / ".liteagents" / "attempts"):
                            raise ConfigurationError(
                                "Refusing to remove a checkpoint outside the owned attempt directory"
                            )
                        if path.exists():
                            await asyncio.to_thread(shutil.rmtree, path)
                await store.db.execute("DELETE FROM la_runs WHERE run_key=?", key)
                removed += 1
    return removed
