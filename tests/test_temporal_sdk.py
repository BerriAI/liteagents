import asyncio
import os
import socket
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from liteagents import (
    ConfigurationError,
    LiteAgentClient,
    LiteAgentOptions,
    ProfileOptions,
    RunAlreadyExistsError,
    RunNotFoundError,
    TemporalOptions,
)
from liteagents import (
    run as run_agent,
)
from tests.test_python_harnesses import Lookup, deep_model, pydantic_model

pytestmark = pytest.mark.integration


@pytest.fixture
def temporal_available():
    pytest.importorskip("temporalio")
    pytest.importorskip("deepagents")
    try:
        with socket.create_connection(("127.0.0.1", 7233), timeout=0.2):
            pass
    except OSError:
        pytest.skip("Start temporal server start-dev on localhost:7233 for integration tests")


async def test_public_sdk_detach_attach_duplicate_and_replay(tmp_path, temporal_available):
    from temporalio.worker import Replayer
    from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

    from liteagents.temporal import LiteAgentWorker
    from liteagents.temporal.client import connect
    from liteagents.temporal.workflows import AgentWorkflow

    tool = Lookup()
    profile = ProfileOptions(
        harness="deepagents",
        model="scripted/test",
        tools=["lookup"],
        system_prompt="worker-only-configuration-sentinel",
        harness_options={"model_instance": deep_model()},
        temporal=TemporalOptions(
            profile_id=f"sdk-test-{uuid4().hex}", checkpoint_path=str(tmp_path / "graph.sqlite")
        ),
    )
    worker = LiteAgentWorker(profile=profile, tools=[tool], cwd=tmp_path)
    run_id = f"sdk-{uuid4().hex}"
    async with worker.running():
        async with LiteAgentClient(options=LiteAgentOptions(profile=profile)) as client:
            submitted = await client.start_run("Look up order A123", run_id=run_id)
            assert submitted.run_id == run_id
        async with LiteAgentClient(options=LiteAgentOptions(profile=profile)) as reconnected:
            run = await reconnected.get_run(run_id)
            result = await asyncio.wait_for(run.result(), 30)
            assert result.text == "Order total: USD 12"
            assert await run.status() == "completed"
            with pytest.raises(RunAlreadyExistsError):
                await reconnected.start_run("must not run", run_id=run_id)
            with pytest.raises(RunNotFoundError):
                await reconnected.get_run("absent-" + uuid4().hex)
        with pytest.raises(ConfigurationError, match="owns"):
            async with LiteAgentWorker(profile=profile, tools=[tool], cwd=tmp_path).running():
                pytest.fail("Second worker acquired the same store")
    assert tool.calls == [{"order": "A123"}]
    connection = await connect(profile)
    history = await connection.get_workflow_handle(run_id).fetch_history()
    raw = b"".join(event.SerializeToString() for event in history.events)
    assert b"worker-only-configuration-sentinel" not in raw
    runner = SandboxedWorkflowRunner(
        restrictions=SandboxRestrictions.default.with_passthrough_modules("liteagents")
    )
    await Replayer(workflows=[AgentWorkflow], workflow_runner=runner).replay_workflow(history)


@pytest.mark.parametrize(
    "harness,factory", [("deepagents", deep_model), ("pydantic-ai", pydantic_model)]
)
async def test_run_helper_with_temporal_worker(tmp_path, temporal_available, harness, factory):
    from liteagents.temporal import LiteAgentWorker

    tool = Lookup()
    profile = ProfileOptions(
        harness=harness, model="scripted/test", tools=["lookup"],
        harness_options={"model_instance": factory()},
        temporal=TemporalOptions(
            profile_id=f"run-helper-{uuid4().hex}",
            checkpoint_path=str(tmp_path / "graph.sqlite"),
        ),
    )
    run_id = f"run-{uuid4().hex}"
    async with LiteAgentWorker(profile=profile, tools=[tool], cwd=tmp_path).running():
        result = await asyncio.wait_for(
            run_agent("Look up order A123", profile=profile, cwd=tmp_path, run_id=run_id), 30,
        )
        assert result.text == "Order total: USD 12"
        assert result.harness == harness and result.run_id == run_id
        assert tool.calls == [{"order": "A123"}]
        with pytest.raises(RunAlreadyExistsError):
            await run_agent("must not resubmit", profile=profile, cwd=tmp_path, run_id=run_id)


@pytest.mark.parametrize("harness", ["deepagents", "pydantic-ai"])
@pytest.mark.parametrize("mode", ["tool", "approval", "child"])
async def test_public_sdk_recovers_after_worker_process_is_killed(
    tmp_path, temporal_available, harness, mode
):
    profile_id = "crash-" + uuid4().hex
    run_id = "sdk-crash-" + uuid4().hex
    pytest.importorskip("pydantic_ai" if harness == "pydantic-ai" else "deepagents")
    db = tmp_path / "checkpoints.sqlite"
    marker = tmp_path / "calls.txt"
    profile = ProfileOptions(
        harness=harness,
        model="scripted/test",
        temporal=TemporalOptions(
            profile_id=profile_id,
            checkpoint_path=str(db),
            heartbeat_timeout_seconds=3,
            activity_timeout_seconds=60,
        ),
    )
    command = [
        sys.executable,
        str(Path(__file__).with_name("temporal_worker_fixture.py")),
        str(tmp_path),
        profile_id,
        harness,
        mode,
    ]
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(__file__).resolve().parents[1]),
        LANGSMITH_TRACING="false",
        LANGCHAIN_TRACING_V2="false",
    )
    processes = []
    log = (tmp_path / "worker.log").open("w")
    handle = None
    try:
        processes.append(
            await asyncio.create_subprocess_exec(
                *command, env=env, stdout=log, stderr=asyncio.subprocess.STDOUT
            )
        )
        async with LiteAgentClient(options=LiteAgentOptions(profile=profile)) as client:
            handle = await client.start_run("Read and validate the order", run_id=run_id)
        async with asyncio.timeout(45):
            if mode == "approval":
                async for event in handle.events():
                    if event.kind == "approval_requested":
                        approval_id = event.data["id"]
                        break
                assert marker.read_text().splitlines() == ["lookup"]
            else:
                while not marker.exists() or "slow-start" not in marker.read_text():
                    assert processes[0].returncode is None, (tmp_path / "worker.log").read_text()
                    await asyncio.sleep(0.1)
        processes[0].kill()
        await asyncio.wait_for(processes[0].wait(), 5)
        if mode == "approval":
            async with LiteAgentClient(options=LiteAgentOptions(profile=profile)) as client:
                attached = await client.get_run(run_id)
                assert (await attached.approvals())[0]["id"] == approval_id
                await attached.approve(approval_id)
        processes.append(
            await asyncio.create_subprocess_exec(
                *command, env=env, stdout=log, stderr=asyncio.subprocess.STDOUT
            )
        )
        async with LiteAgentClient(options=LiteAgentOptions(profile=profile)) as client:
            result = await asyncio.wait_for((await client.get_run(run_id)).result(), 45)
        assert result.text == "Validated USD 12"
        calls = marker.read_text().splitlines()
        assert calls.count("lookup") == 1
        assert calls.count("slow-start") == (1 if mode == "approval" else 2)
        assert calls.count("slow-done") == 1
    finally:
        for process in processes:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except TimeoutError:
                    process.kill()
                    await asyncio.wait_for(process.wait(), 5)
        log.close()
        if handle is not None and await handle.status() == "running":
            await handle.handle.terminate("Integration test cleanup")


@pytest.mark.parametrize(
    "harness,factory",
    [
        ("deepagents", deep_model),
        (
            "pydantic-ai",
            __import__("tests.test_python_harnesses", fromlist=["pydantic_model"]).pydantic_model,
        ),
    ],
)
async def test_postgres_shared_workers_reconnect_to_approval_and_events(
    tmp_path, temporal_available, harness, factory
):
    from liteagents.temporal import LiteAgentWorker

    url = os.environ.get("LITEAGENTS_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set LITEAGENTS_TEST_POSTGRES_URL")
    tool = Lookup()
    profile = ProfileOptions(
        harness=harness,
        model="scripted/test",
        tools=["lookup"],
        harness_options={"model_instance": factory(), "interrupt_on": {"lookup": True}},
        temporal=TemporalOptions(
            profile_id="postgres-" + uuid4().hex, state_url=url, checkpoint_url=url
        ),
    )
    worker = LiteAgentWorker(profile=profile, tools=[tool], cwd=tmp_path)
    async with (
        worker.running(),
        LiteAgentWorker(profile=profile, tools=[tool], cwd=tmp_path).running(),
    ):
        async with LiteAgentClient(options=LiteAgentOptions(profile=profile)) as client:
            run = await client.start_run("lookup order", run_id="pg-" + uuid4().hex)
            async with asyncio.timeout(15):
                async for event in run.events():
                    if event.kind == "approval_requested":
                        cursor = event.cursor
                        break
            assert not tool.calls
            assert await run.status() == "waiting_for_approval"
        async with LiteAgentClient(options=LiteAgentOptions(profile=profile)) as client:
            attached = await client.get_run(run.run_id)
            pending = await attached.approvals()
            await attached.approve(pending[0]["id"])
            assert (await asyncio.wait_for(attached.result(), 20)).text == "Order total: USD 12"
            events = [e async for e in attached.events(after=cursor)]
            assert events and all(e.cursor > cursor for e in events)
            assert await attached.approvals() == []
    assert len(tool.calls) == 1


async def test_retention_removes_owned_graph_checkpoints(tmp_path, temporal_available):
    import sqlite3

    from liteagents.temporal import LiteAgentWorker

    profile = ProfileOptions(
        harness="deepagents",
        model="scripted/test",
        tools=["lookup"],
        harness_options={"model_instance": deep_model()},
        temporal=TemporalOptions(
            profile_id="retention-" + uuid4().hex, checkpoint_path=str(tmp_path / "graph.sqlite")
        ),
    )
    worker = LiteAgentWorker(profile=profile, tools=[Lookup()], cwd=tmp_path)
    async with (
        worker.running(),
        LiteAgentClient(options=LiteAgentOptions(profile=profile)) as client,
    ):
        run = await client.start_run("lookup order", run_id="retention-" + uuid4().hex)
        assert (await asyncio.wait_for(run.result(), 20)).text
    with sqlite3.connect(tmp_path / "graph.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] > 0
    assert await worker.purge(older_than_days=0) == 1
    with sqlite3.connect(tmp_path / "graph.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] == 0
    with pytest.raises(RunNotFoundError):
        await run.result()
    assert await run.handle.result()  # Only a small state reference is in Temporal history.


async def test_cancel_before_worker_dispatch_and_wrong_version(tmp_path, temporal_available):
    from temporalio.client import WorkflowFailureError

    from liteagents.temporal import LiteAgentWorker

    tool = Lookup()
    profile = ProfileOptions(
        harness="deepagents",
        model="scripted/test",
        tools=["lookup"],
        harness_options={"model_instance": deep_model()},
        temporal=TemporalOptions(
            profile_id="cancel-" + uuid4().hex, checkpoint_path=str(tmp_path / "graph.sqlite")
        ),
    )
    worker = LiteAgentWorker(profile=profile, tools=[tool], cwd=tmp_path)
    async with LiteAgentClient(options=LiteAgentOptions(profile=profile)) as client:
        run = await client.start_run("lookup", run_id="cancel-" + uuid4().hex)
        await run.cancel()
        wrong = profile.model_copy(deep=True)
        wrong.temporal.profile_id = "incorrect-version"
        async with LiteAgentClient(options=LiteAgentOptions(profile=wrong)) as other:
            attached = await other.get_run(run.run_id)
            with pytest.raises(ConfigurationError, match="version"):
                await attached.cancel()
        async with worker.running():
            with pytest.raises(WorkflowFailureError):
                await asyncio.wait_for(run.result(), 15)
            assert await run.status() == "cancelled"
            assert (await run.state())["status"] == "cancelled"
    assert not tool.calls
    assert await worker.purge(older_than_days=0) == 1


async def test_postgres_retention_removes_only_owned_graph(tmp_path, temporal_available):
    from liteagents.temporal import LiteAgentWorker

    url = os.environ.get("LITEAGENTS_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set LITEAGENTS_TEST_POSTGRES_URL")
    profile = ProfileOptions(
        harness="deepagents",
        model="scripted/test",
        tools=["lookup"],
        harness_options={"model_instance": deep_model()},
        temporal=TemporalOptions(
            profile_id="pg-retention-" + uuid4().hex, state_url=url, checkpoint_url=url
        ),
    )
    worker = LiteAgentWorker(profile=profile, tools=[Lookup()], cwd=tmp_path)
    async with (
        worker.running(),
        LiteAgentClient(options=LiteAgentOptions(profile=profile)) as client,
    ):
        run = await client.start_run("lookup order", run_id="pg-retention-" + uuid4().hex)
        assert (await asyncio.wait_for(run.result(), 20)).text
    import json

    records = await worker.store.db.execute(
        "SELECT value FROM la_values WHERE run_key=? AND name LIKE 'checkpoint:%'", run.key
    )
    session = json.loads(records[0][0])["session"]
    rows = await worker.store.db.execute(
        "SELECT COUNT(*) FROM checkpoints WHERE thread_id=?", session
    )
    assert rows[0][0] > 0
    assert await worker.purge(older_than_days=0) == 1
    rows = await worker.store.db.execute(
        "SELECT COUNT(*) FROM checkpoints WHERE thread_id=?", session
    )
    assert rows[0][0] == 0


@pytest.mark.parametrize("expired", [False, True])
async def test_retention_reconciles_terminated_run_without_worker(
    tmp_path, temporal_available, expired
):
    from liteagents.temporal import LiteAgentWorker

    profile = ProfileOptions(
        harness="deepagents",
        model="scripted/test",
        harness_options={"model_instance": deep_model()},
        temporal=TemporalOptions(
            profile_id="terminated-" + uuid4().hex, checkpoint_path=str(tmp_path / "graph.sqlite")
        ),
    )
    worker = LiteAgentWorker(profile=profile, cwd=tmp_path)
    async with LiteAgentClient(options=LiteAgentOptions(profile=profile)) as client:
        run = await client.start_run("never dispatched", run_id="terminated-" + uuid4().hex)
        await run.handle.terminate("Retention test")
        assert (await run.state())["status"] == "running"
        if expired:
            # Simulate history that has already disappeared from Temporal.
            await worker.store.put(run.key, "workflow_id", "absent-" + uuid4().hex)
    assert await worker.purge(older_than_days=0) == 1
    with pytest.raises(RunNotFoundError):
        await run.state()
