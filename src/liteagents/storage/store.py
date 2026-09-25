"""Durable run state, replay records, approval decisions, and cursor-based events."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from ..errors import ConfigurationError, RunNotFoundError
from .sql import Database


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


class RunStore:
    def __init__(
        self,
        url: str,
        cwd: Path = Path("."),
        *,
        max_events: int = 20_000,
        max_bytes: int = 8_000_000,
    ):
        self.db = Database(url, cwd)
        self.max_events = max_events
        self.max_bytes = max_bytes

    async def setup(self) -> None:
        await self.db.setup()

    def encode(self, value: Any) -> str:
        encoded = json.dumps(value, separators=(",", ":"))
        if len(encoded.encode()) > self.max_bytes:
            raise ConfigurationError(
                "Stored payload exceeds max_payload_bytes; use an external artifact"
            )
        return encoded

    async def ensure_run(self, key: str, profile_id: str) -> dict[str, Any]:
        await self.db.execute(
            "INSERT INTO la_runs(run_key,profile_id,status,updated) VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
            key,
            profile_id,
            "running",
            time.time(),
        )
        row = await self.get_run(key)
        if row["profile_id"] != profile_id:
            raise ConfigurationError("Run profile version does not match this worker")
        return row

    async def get_run(self, key: str) -> dict[str, Any]:
        rows = await self.db.execute(
            "SELECT profile_id,status,result,error,tool_started,cancelled FROM la_runs WHERE run_key=?",
            key,
        )
        if not rows:
            raise RunNotFoundError("Run state is unavailable or has expired")
        p, status, result, error, started, cancelled = rows[0]
        return {
            "profile_id": p,
            "status": status,
            "result": json.loads(result) if result else None,
            "error": error,
            "tool_started": bool(started),
            "cancelled": bool(cancelled),
        }

    async def set_status(
        self, key: str, status: str, *, result: Any = None, error: str | None = None
    ) -> None:
        await self.db.execute(
            "UPDATE la_runs SET status=?,result=COALESCE(?,result),error=?,updated=? WHERE run_key=?",
            status,
            self.encode(result) if result is not None else None,
            error,
            time.time(),
            key,
        )

    async def mark_tool(self, key: str) -> None:
        await self.db.execute(
            "UPDATE la_runs SET tool_started=1,updated=? WHERE run_key=?", time.time(), key
        )

    async def cancel(self, key: str) -> None:
        await self.db.execute(
            "UPDATE la_runs SET cancelled=1,updated=? WHERE run_key=?", time.time(), key
        )

    async def get(self, key: str, name: str) -> Any:
        rows = await self.db.execute(
            "SELECT value FROM la_values WHERE run_key=? AND name=?", key, name
        )
        return json.loads(rows[0][0]) if rows else None

    async def put(self, key: str, name: str, value: Any, *, replace: bool = True) -> None:
        conflict = "DO UPDATE SET value=excluded.value" if replace else "DO NOTHING"
        await self.db.execute(
            f"INSERT INTO la_values(run_key,name,value) VALUES (?,?,?) ON CONFLICT(run_key,name) {conflict}",
            key,
            name,
            self.encode(value),
        )

    async def event(self, key: str, event_key: str, payload: dict[str, Any]) -> None:
        encoded = self.encode(payload)
        # Lock the run row in the same transaction as the bound check. Parallel
        # tool callbacks and separate clients must observe the same event limit.
        rows = await self.db.transaction(
            [
                ("UPDATE la_runs SET updated=updated WHERE run_key=?", (key,)),
                (
                    "INSERT INTO la_events(run_key,event_key,payload) SELECT ?,?,? WHERE (SELECT COUNT(*) FROM la_events WHERE run_key=?)<? ON CONFLICT DO NOTHING",
                    (key, event_key, encoded, key, self.max_events),
                ),
                ("SELECT 1 FROM la_events WHERE run_key=? AND event_key=?", (key, event_key)),
            ]
        )
        if not rows[-1]:
            raise ConfigurationError(
                "Run event limit reached; increase max_events or shorten the run"
            )

    async def events(self, key: str, after: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        rows = await self.db.execute(
            "SELECT seq,payload FROM la_events WHERE run_key=? AND seq>? ORDER BY seq LIMIT ?",
            key,
            after,
            min(max(limit, 1), 1000),
        )
        return [{"cursor": seq, **json.loads(payload)} for seq, payload in rows]

    async def approve(self, key: str, approval_id: str, allow: bool) -> None:
        if await self.get(key, "approval:" + approval_id) is None:
            raise ConfigurationError("No such pending approval")
        name = "decision:" + approval_id
        await self.put(key, name, {"allow": allow}, replace=False)
        if (await self.get(key, name))["allow"] != allow:
            raise ConfigurationError("This approval already has a different decision")

    async def pending_approvals(self, key: str) -> list[dict[str, Any]]:
        rows = await self.db.execute(
            "SELECT name,value FROM la_values WHERE run_key=? AND name LIKE 'approval:%' ORDER BY name",
            key,
        )
        return [
            json.loads(value)
            for name, value in rows
            if await self.get(key, "decision:" + name.removeprefix("approval:")) is None
        ]

    async def purge(self, older_than_seconds: float) -> int:
        if older_than_seconds < 0:
            raise ConfigurationError("Retention duration must be nonnegative")
        rows = await self.db.execute(
            "DELETE FROM la_runs WHERE status IN ('completed','failed','cancelled') AND updated<? RETURNING run_key",
            time.time() - older_than_seconds,
        )
        return len(rows)
