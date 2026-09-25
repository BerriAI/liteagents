"""Tie an owned native server's lifetime to its parent's stdin pipe (POSIX)."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from contextlib import suppress


async def supervise(command: list[str]) -> int:
    process = await asyncio.create_subprocess_exec(
        *command, stdin=asyncio.subprocess.DEVNULL, start_new_session=True
    )
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopping.set)
    reader = asyncio.StreamReader()
    transport, _ = await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer
    )
    waiting = asyncio.create_task(process.wait())
    orphaned = asyncio.create_task(reader.read(1))
    stopped = asyncio.create_task(stopping.wait())
    try:
        await asyncio.wait([waiting, orphaned, stopped], return_when=asyncio.FIRST_COMPLETED)
    finally:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(asyncio.shield(waiting), 2)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                await waiting
        orphaned.cancel()
        stopped.cancel()
        await asyncio.gather(orphaned, stopped, return_exceptions=True)
        transport.close()
    return process.returncode or 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(supervise(sys.argv[1:])))
