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
from tests.test_python_harnesses import Lookup, deep_model

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


async def test_public_sdk_recovers_after_worker_process_is_killed(tmp_path, temporal_available):
    profile_id = "crash-" + uuid4().hex
    run_id = "sdk-crash-" + uuid4().hex
    db = tmp_path / "checkpoints.sqlite"
    marker = tmp_path / "calls.txt"
    profile = ProfileOptions(
        harness="deepagents",
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
            while not marker.exists() or "slow-start" not in marker.read_text():
                assert processes[0].returncode is None, (tmp_path / "worker.log").read_text()
                await asyncio.sleep(0.1)
        processes[0].kill()
        await asyncio.wait_for(processes[0].wait(), 5)
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
        assert calls.count("slow-start") == 2
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
