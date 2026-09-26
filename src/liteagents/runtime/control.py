"""Run-scoped operation boundaries shared by native harness adapters."""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from ..errors import ConfigurationError, HarnessError
from ..profiles import ProfileOptions
from ..storage.store import RunStore, digest

CURRENT: contextvars.ContextVar[RunControl | None] = contextvars.ContextVar(
    "liteagents_run", default=None
)
SCOPE: contextvars.ContextVar[str] = contextvars.ContextVar("liteagents_scope", default="root")
OPERATION_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "liteagents_operation", default=None
)


class OperationFailed(HarnessError):
    """A recorded terminal operation failure; worker retries cannot reset its budget."""

    def __init__(self, message: str, *, transient: bool):
        super().__init__(message)
        self.transient = transient


def operation_failed(error: BaseException) -> bool:
    return isinstance(error, OperationFailed) or (
        error.__cause__ is not None and operation_failed(error.__cause__)
    )


def operation_id() -> str | None:
    """Stable idempotency key while an application tool executes."""
    return OPERATION_ID.get()


def retryable(error: BaseException) -> bool:
    import httpx

    if isinstance(error, OperationFailed):
        return error.transient
    if isinstance(error, (ConfigurationError, PermissionError, ValueError)):
        return False
    if getattr(error, "code", None) == "liteagents_transient_stream":
        return True
    status = getattr(error, "status_code", None)
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
    if status is not None:
        return status == 429 or 500 <= status < 600
    if isinstance(error, (TimeoutError, ConnectionError, httpx.TransportError)):
        return True
    return error.__cause__ is not None and retryable(error.__cause__)


def stable(value: Any) -> Any:
    """Remove native message metadata while retaining IDs inside application inputs."""
    if isinstance(value, (tuple, list)):
        return [stable(v) for v in value]
    if isinstance(value, dict):
        result = {k: v for k, v in value.items() if k not in ("timestamp", "run_id")}
        if "data" in result and "type" in result:
            result["data"] = {
                k: v
                for k, v in result["data"].items()
                if k not in ("id", "response_metadata", "usage_metadata")
            }
        if "parts" in result:
            result["parts"] = [
                {k: v for k, v in part.items() if k != "timestamp"} for part in result["parts"]
            ]
        return result
    return value


class RunControl:
    def __init__(
        self,
        profile: ProfileOptions,
        *,
        run_key: str,
        store: RunStore | None = None,
        emit: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        check_owner: Callable[[], Awaitable[None]] | None = None,
    ):
        self.profile = profile
        self.run_key = run_key
        self.store = store
        self.emit_callback = emit
        self.check_owner = check_owner
        self.values: dict[str, Any] = {}
        self.tool_started = False
        self.cancelled = False
        self.locks: dict[str, asyncio.Lock] = {}

    async def get(self, name: str) -> Any:
        return await self.store.get(self.run_key, name) if self.store else self.values.get(name)

    async def put(self, name: str, value: Any) -> None:
        if self.store:
            await self.store.put(self.run_key, name, value)
        else:
            self.values[name] = value

    async def emit(self, kind: str, **fields: Any) -> None:
        event = {"kind": kind, "scope": SCOPE.get(), **fields}
        if self.emit_callback:
            await self.emit_callback(event)
        elif self.store:
            await self.store.event(self.run_key, uuid4().hex, event)

    async def check(self) -> None:
        if self.check_owner:
            await self.check_owner()
        if self.cancelled or self.store and (await self.store.get_run(self.run_key))["cancelled"]:
            raise asyncio.CancelledError("Run cancellation requested")

    async def approval(self, name: str, arguments: dict[str, Any], key: str) -> None:
        approvals = self.profile.harness_options.get("interrupt_on", {})
        if not approvals.get(name):
            return
        approval_id = digest([self.run_key, key])[:32]
        record = {"id": approval_id, "tool": name, "arguments": arguments}
        if await self.get("approval:" + approval_id) is None:
            await self.put("approval:" + approval_id, record)
            await self.emit("approval_requested", **record)
        if self.store:
            await self.store.set_status(self.run_key, "waiting_for_approval")
        while (decision := await self.get("decision:" + approval_id)) is None:
            await self.check()
            await asyncio.sleep(0.1)
        if self.store:
            await self.store.set_status(
                self.run_key, "waiting_for_approval" if await self.pending() else "running"
            )
        if not decision["allow"]:
            raise PermissionError(f"Execution of {name} was denied")

    async def approve(self, approval_id: str, allow: bool) -> None:
        if self.store:
            await self.store.approve(self.run_key, approval_id, allow)
        else:
            if "approval:" + approval_id not in self.values:
                raise ConfigurationError("No such pending approval")
            name = "decision:" + approval_id
            if name in self.values and self.values[name]["allow"] != allow:
                raise ConfigurationError("This approval already has a different decision")
            self.values[name] = {"allow": allow}

    async def pending(self) -> list[dict[str, Any]]:
        if self.store:
            return await self.store.pending_approvals(self.run_key)
        return [
            v
            for k, v in self.values.items()
            if k.startswith("approval:") and "decision:" + k[9:] not in self.values
        ]

    async def call(
        self,
        kind: str,
        key: str,
        arguments: Any,
        function: Callable[[], Awaitable[Any]],
        *,
        encode: Callable[[Any], Any] = lambda x: x,
        decode: Callable[[Any], Any] = lambda x: x,
        max_attempts: int | None = None,
    ) -> Any:
        name = f"{SCOPE.get()}:{kind}:{key}"
        async with self.locks.setdefault(name, asyncio.Lock()):
            await self.check()
            saved = await self.get("result:" + name)
            fingerprint = digest(arguments)
            previous = await self.get("input:" + name)
            if previous is not None and previous != fingerprint:
                raise ConfigurationError("An operation ID was reused with different inputs")
            await self.put("input:" + name, fingerprint)
            if saved is not None:
                if saved["fingerprint"] != fingerprint:
                    raise ConfigurationError("An operation ID was reused with different inputs")
                return decode(saved["value"])
            failure = await self.get("failure:" + name)
            if failure is not None:
                if failure["fingerprint"] != fingerprint:
                    raise ConfigurationError("An operation ID was reused with different inputs")
                raise OperationFailed(failure["message"], transient=failure["transient"])
            if kind == "tool":
                self.tool_started = True
                if self.store:
                    await self.store.mark_tool(self.run_key)
                await self.approval(arguments["name"], arguments["input"], name)
            policy = self.profile.recovery
            limit = max_attempts or (
                policy.retries.max_attempts
                if policy
                else self.profile.temporal.worker_recovery_attempts
                if self.store and self.profile.temporal
                else 1
            )
            attempts = await self.get("attempts:" + name) or 0
            if attempts >= limit:
                raise OperationFailed(
                    f"{kind} operation exhausted its {limit} attempts", transient=True
                )
            while attempts < limit:
                await self.check()
                attempts += 1
                await self.put("attempts:" + name, attempts)
                token = OPERATION_ID.set(digest([self.run_key, name]))
                scope_token = SCOPE.set(SCOPE.get() + "/tool:" + key) if kind == "tool" else None
                try:
                    result = await function()
                    await self.check()
                    await self.put(
                        "result:" + name, {"fingerprint": fingerprint, "value": encode(result)}
                    )
                    return result
                except Exception as exc:
                    if attempts >= limit or not retryable(exc):
                        transient = retryable(exc)
                        message = f"{kind} operation failed after {attempts} attempt(s): {type(exc).__name__}"
                        await self.put(
                            "failure:" + name,
                            {
                                "fingerprint": fingerprint,
                                "message": message,
                                "transient": transient,
                            },
                        )
                        raise OperationFailed(message, transient=transient) from exc
                    await self.emit("operation_retry", operation=kind, attempt=attempts + 1)
                    await asyncio.sleep(min(0.25 * 2 ** (attempts - 1), 2))
                finally:
                    OPERATION_ID.reset(token)
                    if scope_token is not None:
                        SCOPE.reset(scope_token)
            raise AssertionError("unreachable")
