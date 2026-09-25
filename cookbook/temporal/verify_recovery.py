"""Kill only workers created here. Requires the local Temporal server to be running."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from connection import connect
from temporalio.common import WorkflowIDReusePolicy

ROOT = Path(__file__).resolve().parent


async def main() -> None:
    if os.environ.get("TEMPORAL_ADDRESS", "localhost:7233") not in (
        "localhost:7233",
        "127.0.0.1:7233",
    ):
        raise RuntimeError("Run this fault-injection demo against the local development server.")
    client = await connect()
    run_id = f"recovery-{uuid4().hex[:12]}"
    queue = f"liteagents-{run_id}"
    directory = ROOT / ".state" / run_id
    directory.mkdir(parents=True)
    env = dict(
        os.environ,
        TEMPORAL_TASK_QUEUE=queue,
        LITEAGENTS_CHECKPOINT_DB=str(directory / "checkpoints.sqlite"),
    )
    env.pop("DEEPAGENTS_MODEL", None)  # Always free and repeatable.
    env["LANGSMITH_TRACING"] = "false"
    env["LANGCHAIN_TRACING_V2"] = "false"
    logs = [directory / "worker-1.log", directory / "worker-2.log"]
    processes = []
    streams = []

    def start_worker(index: int) -> subprocess.Popen:
        stream = logs[index].open("w")
        streams.append(stream)
        process = subprocess.Popen(
            [sys.executable, "-u", str(ROOT / "worker.py")],
            env=env,
            cwd=ROOT,
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
        processes.append(process)
        return process

    handle = None
    try:
        first = start_worker(0)
        handle = await client.start_workflow(
            "DeepAgentWorkflow",
            "Read order A123, validate it, then summarize.",
            id=run_id,
            task_queue=queue,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        )
        async with asyncio.timeout(60):
            while "TOOL_START validate_order" not in logs[0].read_text():
                if first.poll() is not None:
                    raise RuntimeError(logs[0].read_text())
                await asyncio.sleep(0.2)
        first.kill()
        first.wait(timeout=5)
        print("Killed the worker during validate_order, after read_order was checkpointed.")
        start_worker(1)
        result = await asyncio.wait_for(handle.result(), timeout=60)
        combined = "\n".join(path.read_text() for path in logs)
        assert combined.count("TOOL read_order") == 1, combined
        assert combined.count("MODEL read_order") == 1, combined
        assert combined.count("MODEL validate_order") == 1, combined
        assert combined.count("TOOL_START validate_order") == 2, combined
        assert "ACTIVITY attempt=2" in combined, combined
        assert "CHECKPOINT resume" in combined, combined
        assert result == "Order A123: 2 notebooks, USD 12. Validation passed.", result
        print(f"PASS: {run_id}")
        print(
            "Completed model/tool steps ran once; interrupted tool ran twice; activity attempt 2 resumed."
        )
        print(result)
        print(f"Evidence: {directory}")
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for stream in streams:
            stream.close()
        if handle and (await handle.describe()).status.name == "RUNNING":
            await handle.terminate("Recovery verification cleanup")


if __name__ == "__main__":
    asyncio.run(main())
