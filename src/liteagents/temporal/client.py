from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode, TLSConfig

from ..errors import (
    ConfigurationError,
    RunAlreadyExistsError,
    RunNotFoundError,
    UnsupportedFeatureError,
)
from ..harnesses import get_capabilities
from ..profiles import ProfileOptions
from ..tools import Tool
from ..types import AgentEvent, Message
from .handles import TemporalRun
from .state import run_key, run_store


async def connect(profile: ProfileOptions) -> Client:
    options = profile.temporal
    assert options is not None
    tls: bool | TLSConfig = options.tls or bool(options.api_key)
    if (
        options.tls_server_root_ca
        or options.tls_client_cert
        or options.tls_client_key
        or options.tls_server_name
    ):
        if bool(options.tls_client_cert) != bool(options.tls_client_key):
            raise ConfigurationError("Provide both tls_client_cert and tls_client_key")
        tls = TLSConfig(
            server_root_ca_cert=Path(options.tls_server_root_ca).read_bytes()
            if options.tls_server_root_ca
            else None,
            client_cert=Path(options.tls_client_cert).read_bytes()
            if options.tls_client_cert
            else None,
            client_private_key=Path(options.tls_client_key).read_bytes()
            if options.tls_client_key
            else None,
            domain=options.tls_server_name,
        )
    return await Client.connect(
        options.address,
        namespace=options.namespace,
        api_key=options.api_key,
        tls=tls,
    )


def queue_name(profile: ProfileOptions) -> str:
    """A worker must never consume a different profile's tasks on a shared queue."""
    import hashlib

    assert profile.temporal is not None
    suffix = hashlib.sha256(profile.identity().encode()).hexdigest()[:16]
    return f"{profile.temporal.task_queue}-{suffix}"


class TemporalRuntime:
    def __init__(
        self, profile: ProfileOptions, *, cwd: Path, tools: list[Tool], session_id: str | None
    ):
        if not get_capabilities(profile.harness).temporal:
            raise UnsupportedFeatureError(
                f"Temporal recovery is not yet verified for {profile.harness}"
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
        self.store = run_store(profile, cwd)
        self.history: list[Message] = []
        self.client: Client | None = None

    async def open(self) -> None:
        await self.store.setup()
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
        if len(prompt.encode()) > 1_000_000:
            raise ConfigurationError(
                "Prompt exceeds the Temporal payload bound; pass an artifact reference"
            )
        key = run_key(self.profile, run_id)
        try:
            await self.store.get_run(key)
        except RunNotFoundError:
            pass
        else:
            raise RunAlreadyExistsError(f"Run {run_id!r} already has retained SDK state")
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
        await self.store.ensure_run(key, self.profile_id)
        await self.store.put(key, "workflow_id", run_id)
        return TemporalRun(handle, self.store, key, self.profile_id)

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
        return TemporalRun(handle, self.store, run_key(self.profile, run_id), self.profile_id)

    async def query(self, prompt: str, *, run_id: str | None = None) -> AsyncIterator[AgentEvent]:
        run = await self.start_run(prompt, run_id=run_id)
        async for event in run.events():
            message = event.message
            if message is not None:
                yield message
        result = await run.result()
        self.history.extend(result.messages)
