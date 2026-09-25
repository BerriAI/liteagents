import asyncio
import shutil

import pytest

from liteagents import LiteAgentClient, LiteAgentOptions, ProfileOptions
from tests.native_provider_fixture import Provider
from tests.operation_fixture import FixtureTool

pytestmark = pytest.mark.integration
HARNESSES = ["claude-sdk", "codex", "opencode-v1", "opencode-v2"]


def available(harness):
    if harness.startswith("opencode"):
        if not shutil.which("opencode"):
            pytest.skip("Install OpenCode 1.18.29")
    else:
        pytest.importorskip("claude_agent_sdk" if harness == "claude-sdk" else "openai_codex")


def profile(harness, url):
    return ProfileOptions(
        harness=harness,
        model="litellm_proxy/scripted",
        model_kwargs={"api_base": url, "api_key": "test"},
        tools=["lookup", "slow"],
        recovery={"retries": {"max_attempts": 2}},
        harness_options={"timeout_seconds": 45},
        system_prompt="Use lookup, then slow, then report the result. Use only those tools.",
    )


@pytest.mark.parametrize("harness", HARNESSES)
async def test_real_native_process_uses_managed_tools(harness, tmp_path):
    available(harness)
    tools = [FixtureTool(name, tmp_path / "calls.txt") for name in ("lookup", "slow")]
    async with Provider().running() as upstream, asyncio.timeout(70):
        async with LiteAgentClient(
            options=LiteAgentOptions(
                profile=profile(harness, upstream.url), tools=tools, cwd=tmp_path
            )
        ) as client:
            run = await client.start_run("Read and validate the order")
            result = await run.result()
        assert result.text == "Validated USD 12"
        assert (tmp_path / "calls.txt").read_text().splitlines() == [
            "lookup",
            "slow-start",
            "slow-done",
        ]
        assert len(upstream.requests) >= 3


@pytest.mark.parametrize("harness", HARNESSES)
@pytest.mark.parametrize("mode", ["tool", "approval", "child"])
async def test_native_worker_crash_reuses_completed_operations(harness, tmp_path, mode):
    import os
    import signal
    import socket
    import sys
    from pathlib import Path
    from uuid import uuid4

    from liteagents import TemporalOptions

    available(harness)
    pytest.importorskip("temporalio")
    try:
        with socket.create_connection(("127.0.0.1", 7233), timeout=0.2):
            pass
    except OSError:
        pytest.skip("Start Temporal on localhost:7233")
    version = "native-crash-" + uuid4().hex
    run_id = "native-" + uuid4().hex
    marker = tmp_path / "calls.txt"
    processes = []
    handle = None
    log = (tmp_path / "worker.log").open("w")
    async with Provider().running() as upstream:
        config = profile(harness, upstream.url)
        config.recovery.retries.max_attempts = 3
        config.temporal = TemporalOptions(
            profile_id=version,
            checkpoint_path=str(tmp_path / "checkpoint.sqlite"),
            heartbeat_timeout_seconds=3,
            activity_timeout_seconds=90,
        )
        command = [
            sys.executable,
            str(Path(__file__).with_name("native_worker_fixture.py")),
            str(tmp_path),
            version,
            harness,
            upstream.url,
            mode,
        ]
        env = dict(
            os.environ,
            PYTHONPATH=str(Path(__file__).resolve().parents[1]),
            LANGSMITH_TRACING="false",
            LANGCHAIN_TRACING_V2="false",
            PYDANTIC_AI_NO_BANNER="1",
        )

        async def start():
            process = await asyncio.create_subprocess_exec(
                *command,
                env=env,
                stdout=log,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
            processes.append(process)

        try:
            await start()
            async with LiteAgentClient(options=LiteAgentOptions(profile=config)) as client:
                handle = await client.start_run("Read and validate the order", run_id=run_id)
            async with asyncio.timeout(50):
                if mode == "approval":
                    async for event in handle.events():
                        if event.kind == "approval_requested":
                            approval_id = event.data["id"]
                            break
                    assert marker.read_text().splitlines() == ["lookup"]
                else:
                    while not marker.exists() or "slow-start" not in marker.read_text():
                        assert processes[0].returncode is None, (
                            tmp_path / "worker.log"
                        ).read_text()
                        await asyncio.sleep(0.1)
            processes[0].kill()
            await asyncio.wait_for(processes[0].wait(), 5)
            if mode == "approval":
                async with LiteAgentClient(options=LiteAgentOptions(profile=config)) as client:
                    attached = await client.get_run(run_id)
                    assert (await attached.approvals())[0]["id"] == approval_id
                    await attached.approve(approval_id)
            await start()
            try:
                result = await asyncio.wait_for(handle.result(), 60)
            except Exception:
                print((tmp_path / "worker.log").read_text())
                raise
            assert result.text == "Validated USD 12"
            calls = marker.read_text().splitlines()
            assert calls.count("lookup") == 1
            assert calls.count("slow-start") == (1 if mode == "approval" else 2)
            assert calls.count("slow-done") == 1
            # Completed model operations, including the two tool requests, are replayed.
            main = [r for r in upstream.requests if r.get("tools")]
            assert len(main) == (5 if mode == "child" else 3)
            if mode == "tool":
                from liteagents.temporal import LiteAgentWorker

                assert list((tmp_path / ".liteagents" / "attempts").iterdir())
                worker = LiteAgentWorker(
                    profile=config,
                    tools=[FixtureTool(name, marker) for name in ("lookup", "slow")],
                    cwd=tmp_path,
                )
                assert await worker.purge(older_than_days=0) == 1
                assert not list((tmp_path / ".liteagents" / "attempts").iterdir())
        finally:
            for process in processes:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except TimeoutError:
                    os.killpg(process.pid, signal.SIGKILL)
                    await process.wait()
            log.close()
            if handle is not None and (await handle.handle.describe()).status.name == "RUNNING":
                await handle.handle.terminate("Crash test cleanup")


@pytest.mark.parametrize("harness", HARNESSES)
async def test_native_subagent_model_and_tool_restriction(harness, tmp_path):
    available(harness)
    tools = [FixtureTool(name, tmp_path / "calls.txt") for name in ("lookup", "slow")]
    async with Provider().running() as upstream, asyncio.timeout(70):
        config = profile(harness, upstream.url)
        config.tools = ["read_file"]
        config.features.subagents = True
        from liteagents.profiles import SubagentOptions

        config.subagents = {
            "auditor": SubagentOptions(
                description="Audit the order", model="litellm_proxy/child", tools=["lookup"]
            )
        }
        async with LiteAgentClient(
            options=LiteAgentOptions(profile=config, tools=tools, cwd=tmp_path)
        ) as client:
            run = await client.start_run("Delegate the order audit")
            assert (await run.result()).text == "Validated USD 12"
            events = [e async for e in run.events()]
        assert (tmp_path / "calls.txt").read_text().splitlines() == ["lookup"]
        assert any(e.kind == "subagent_completed" and e.data["agent"] == "auditor" for e in events)
        child_requests = [
            r for r in upstream.requests if r.get("model") == "child" and r.get("tools")
        ]
        assert child_requests
        from liteagents.runtime.native_protocol import tool_name

        names = [
            tool_name(tool)
            for request in child_requests
            for definition in request["tools"]
            for tool in definition.get("tools", [definition])
        ]
        assert all(n.endswith("lookup") for n in names)


@pytest.mark.parametrize("harness", HARNESSES)
@pytest.mark.parametrize("decision", ["deny", "cancel"])
async def test_native_approval_denial_and_cancellation_do_not_dispatch(harness, tmp_path, decision):
    available(harness)
    tools = [FixtureTool(name, tmp_path / "calls.txt") for name in ("lookup", "slow")]
    async with Provider().running() as upstream, asyncio.timeout(30):
        config = profile(harness, upstream.url)
        config.harness_options["interrupt_on"] = {"slow": True}
        async with LiteAgentClient(
            options=LiteAgentOptions(profile=config, tools=tools, cwd=tmp_path)
        ) as client:
            run = await client.start_run("Read and validate the order")
            async for event in run.events():
                if event.kind == "approval_requested":
                    break
            assert await run.status() == "waiting_for_approval"
            assert (tmp_path / "calls.txt").read_text().splitlines() == ["lookup"]
            if decision == "deny":
                await run.approve(event.data["id"], allow=False)
                with pytest.raises(PermissionError):
                    await asyncio.wait_for(run.result(), 10)
            else:
                await run.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(run.result(), 10)
            await asyncio.wait_for(run._settled.wait(), 10)
        assert (tmp_path / "calls.txt").read_text().splitlines() == ["lookup"]


@pytest.mark.parametrize("harness", HARNESSES)
async def test_native_model_retries_then_fallback(harness, tmp_path):
    available(harness)
    tools = [FixtureTool(name, tmp_path / "calls.txt") for name in ("lookup", "slow")]
    async with Provider().running() as upstream, asyncio.timeout(70):
        upstream.fail_models = {"scripted"}
        config = profile(harness, upstream.url)
        config.recovery.model_fallbacks = ["litellm_proxy/backup"]
        async with LiteAgentClient(
            options=LiteAgentOptions(profile=config, tools=tools, cwd=tmp_path)
        ) as client:
            run = await client.start_run("Read and validate the order")
            assert (await run.result()).text == "Validated USD 12"
            events = [event async for event in run.events()]
        assert (tmp_path / "calls.txt").read_text().splitlines() == [
            "lookup",
            "slow-start",
            "slow-done",
        ]
        assert (
            len([r for r in upstream.requests if r.get("tools") and r["model"] == "scripted"]) == 6
        )
        assert len([r for r in upstream.requests if r.get("tools") and r["model"] == "backup"]) == 3
        assert any(e.kind == "operation_retry" for e in events)
        assert any(e.kind == "model_fallback" for e in events)


@pytest.mark.parametrize("harness", HARNESSES)
async def test_native_rejects_unmanaged_model_tool(harness, tmp_path):
    from liteagents.errors import HarnessError

    available(harness)
    async with Provider().running() as upstream, asyncio.timeout(30):
        upstream.forced_tool = "run_shell_command"
        async with LiteAgentClient(
            options=LiteAgentOptions(
                profile=profile(harness, upstream.url),
                tools=[FixtureTool(name, tmp_path / "calls.txt") for name in ("lookup", "slow")],
                cwd=tmp_path,
            )
        ) as client:
            run = await client.start_run("Read and validate the order")
            with pytest.raises(HarnessError, match="model operation failed"):
                await run.result()
        assert not (tmp_path / "calls.txt").exists()
