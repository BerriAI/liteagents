import asyncio
import os
import sys

import pytest


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
async def test_owned_process_exits_when_parent_pipe_disappears(tmp_path):
    marker = tmp_path / "pid"
    command = [
        sys.executable,
        "-m",
        "liteagents.runtime.supervise",
        sys.executable,
        "-c",
        "import os,pathlib,time; pathlib.Path('pid').write_text(str(os.getpid())); time.sleep(60)",
    ]
    process = await asyncio.create_subprocess_exec(
        *command, cwd=tmp_path, stdin=asyncio.subprocess.PIPE
    )
    try:
        async with asyncio.timeout(5):
            while not marker.exists():
                await asyncio.sleep(0.02)
        child = int(marker.read_text())
        process.stdin.close()  # Same EOF delivered when a worker is SIGKILLed.
        await asyncio.wait_for(process.wait(), 5)
        with pytest.raises(ProcessLookupError):
            os.kill(child, 0)
    finally:
        if process.returncode is None:
            process.terminate()
            await asyncio.wait_for(process.wait(), 5)
