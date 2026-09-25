"""Small transactional SQL boundary shared by SQLite and PostgreSQL stores."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

from ..errors import ConfigurationError, MissingDependencyError

SCHEMA_VERSION = 1
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS la_schema (version INTEGER PRIMARY KEY)",
    """CREATE TABLE IF NOT EXISTS la_runs (
        run_key TEXT PRIMARY KEY, profile_id TEXT NOT NULL, status TEXT NOT NULL,
        updated DOUBLE PRECISION NOT NULL, result TEXT, error TEXT,
        tool_started INTEGER NOT NULL DEFAULT 0, cancelled INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS la_values (
        run_key TEXT NOT NULL, name TEXT NOT NULL, value TEXT NOT NULL,
        PRIMARY KEY (run_key, name), FOREIGN KEY (run_key) REFERENCES la_runs(run_key) ON DELETE CASCADE)""",
    """CREATE TABLE IF NOT EXISTS la_events (
        seq {sequence}, run_key TEXT NOT NULL, event_key TEXT NOT NULL, payload TEXT NOT NULL,
        UNIQUE (run_key,event_key), FOREIGN KEY (run_key) REFERENCES la_runs(run_key) ON DELETE CASCADE)""",
    "CREATE INDEX IF NOT EXISTS la_events_run ON la_events (run_key, seq)",
)


class Database:
    def __init__(self, url: str, cwd: Path):
        self.postgres = url.startswith(("postgresql://", "postgres://"))
        self.url = url
        if not self.postgres:
            if not url.startswith("sqlite:///"):
                raise ConfigurationError("Use sqlite:///path or postgresql://... for state_url")
            path = Path(url.removeprefix("sqlite:///"))
            self.path = path if path.is_absolute() else cwd / path
            self.path.parent.mkdir(parents=True, exist_ok=True)

    async def transaction(self, statements: list[tuple[str, tuple[Any, ...]]]) -> list[list[Any]]:
        if self.postgres:
            try:
                import psycopg
            except ImportError as exc:
                raise MissingDependencyError(
                    "Install liteagents[postgres] for PostgreSQL storage"
                ) from exc
            async with await psycopg.AsyncConnection.connect(self.url) as connection:
                results = []
                for sql, params in statements:
                    cursor = await connection.execute(
                        sql.replace("%", "%%").replace("?", "%s"), params
                    )
                    results.append(await cursor.fetchall() if cursor.description else [])
                return results

        def execute():
            with sqlite3.connect(self.path, timeout=30) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("BEGIN IMMEDIATE")
                result = []
                for sql, params in statements:
                    cursor = connection.execute(sql, params)
                    result.append(cursor.fetchall() if cursor.description else [])
                return result

        return await asyncio.to_thread(execute)

    async def execute(self, sql: str, *params: Any) -> list[Any]:
        return (await self.transaction([(sql, params)]))[0]

    async def setup(self) -> None:
        sequence = "BIGSERIAL PRIMARY KEY" if self.postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
        statements = [("SELECT pg_advisory_xact_lock(721693427)", ())] if self.postgres else []
        await self.transaction(statements + [(sql.format(sequence=sequence), ()) for sql in SCHEMA])
        await self.execute(
            "INSERT INTO la_schema(version) SELECT ? WHERE NOT EXISTS (SELECT 1 FROM la_schema) ON CONFLICT DO NOTHING",
            SCHEMA_VERSION,
        )
        versions = await self.execute("SELECT version FROM la_schema")
        if versions != [(SCHEMA_VERSION,)]:
            raise ConfigurationError(
                "Unsupported LiteAgents state schema version; upgrade the SDK before using this database"
            )
