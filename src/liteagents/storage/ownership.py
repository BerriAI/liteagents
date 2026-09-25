"""Exclusive ownership of a run, held for its full execution attempt."""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager

from filelock import FileLock, Timeout

from ..errors import ConfigurationError
from .store import RunStore


@asynccontextmanager
async def own_run(store: RunStore, key: str):
    if store.db.postgres:
        import psycopg

        identifier = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)
        async with await psycopg.AsyncConnection.connect(
            store.db.url, autocommit=True
        ) as connection:
            cursor = await connection.execute("SELECT pg_try_advisory_lock(%s)", (identifier,))
            row = await cursor.fetchone()
            if row is None or not row[0]:
                raise ConfigurationError("Another worker owns this run")

            async def check():
                try:
                    await connection.execute("SELECT 1")
                except psycopg.OperationalError as exc:
                    raise ConnectionError("Run ownership connection was lost") from exc

            try:
                yield check
            finally:
                if not connection.closed:
                    await connection.execute("SELECT pg_advisory_unlock(%s)", (identifier,))
    else:
        lock = FileLock(
            str(store.db.path) + "." + hashlib.sha256(key.encode()).hexdigest()[:24] + ".lock"
        )
        try:
            lock.acquire(timeout=0)
        except Timeout as exc:
            raise ConfigurationError("Another worker owns this run") from exc

        async def check():
            if not lock.is_locked:
                raise ConfigurationError("Run ownership was lost")

        try:
            yield check
        finally:
            lock.release()
