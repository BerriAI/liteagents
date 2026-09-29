"""A small terminal chat loop for generated agent projects."""

from __future__ import annotations

import asyncio
import itertools
import sys
import time
from collections.abc import AsyncIterator, Callable

from ..types import AgentEvent, AssistantMessage, TextBlock, TextDelta, ToolUseBlock

StreamTurn = Callable[[str, str], AsyncIterator[AgentEvent]]
DIM, RED, RESET, CLEAR = "\033[2m", "\033[31m", "\033[0m", "\r\033[K"
FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class Spinner:
    def __init__(self, enabled: bool):
        self.enabled = enabled
        self.task: asyncio.Task[None] | None = None

    async def _spin(self) -> None:
        start = time.monotonic()
        for frame in itertools.cycle(FRAMES):
            sys.stdout.write(f"{CLEAR}{DIM}{frame} Thinking… ({time.monotonic() - start:.0f}s){RESET}")
            sys.stdout.flush()
            await asyncio.sleep(0.1)

    def start(self) -> None:
        if self.enabled and self.task is None:
            self.task = asyncio.create_task(self._spin())

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
            sys.stdout.write(CLEAR)


def tool_line(block: ToolUseBlock) -> str:
    inner = ", ".join(f"{key}={value!r}" for key, value in block.input.items())
    return f"{DIM}⏺ {block.name}({inner[:80]}){RESET}"


async def show_turn(events: AsyncIterator[AgentEvent], *, spinner: Spinner) -> None:
    printed = False
    spinner.start()
    try:
        async for event in events:
            if isinstance(event, TextDelta):
                await spinner.stop()
                sys.stdout.write(event.text)
                printed = True
            elif isinstance(event, AssistantMessage):
                await spinner.stop()
                for block in event.content:
                    if isinstance(block, ToolUseBlock):
                        print(("\n" if printed else "") + tool_line(block))
                if event.stop_reason == "tool_use":
                    printed = False
                    spinner.start()
                elif not printed:
                    sys.stdout.write(
                        "".join(b.text for b in event.content if isinstance(b, TextBlock))
                    )
                    printed = True
            sys.stdout.flush()
    finally:
        await spinner.stop()
    print()


async def chat(name: str, stream_turn: StreamTurn, *, conversation_id: str = "terminal") -> None:
    """Read prompts until EOF; each turn continues the same stored conversation."""
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    print(f"{name} ready. Ctrl+C to exit." if interactive else f"{name} ready.")
    while True:
        try:
            prompt = await asyncio.to_thread(input, "\n> " if interactive else "")
        except EOFError:
            return
        if not prompt.strip():
            continue
        try:
            await show_turn(stream_turn(prompt, conversation_id), spinner=Spinner(interactive))
        except Exception as exc:  # noqa: BLE001 - keep the chat open after a failed turn
            print(f"{RED}Error: {exc}{RESET}")
