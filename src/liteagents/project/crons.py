"""Interval prompts that run inside the agent process."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta

logger = logging.getLogger(__name__)

TurnRunner = Callable[[str, str], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class Cron:
    every: timedelta
    prompt: str
    conversation_id: str

    def __post_init__(self) -> None:
        if self.every.total_seconds() <= 0:
            raise ValueError("Cron interval must be positive")


async def _repeat(cron: Cron, run_turn: TurnRunner) -> None:
    while True:
        await asyncio.sleep(cron.every.total_seconds())
        try:
            reply = await run_turn(cron.prompt, cron.conversation_id)
            logger.info("cron %s: %s", cron.conversation_id, reply)
        except Exception:
            logger.exception("cron %s failed", cron.conversation_id)


async def run_crons(crons: Sequence[Cron], run_turn: TurnRunner) -> None:
    """Run every cron until cancelled. A failing turn is logged and retried next interval."""
    async with asyncio.TaskGroup() as group:
        for cron in crons:
            group.create_task(_repeat(cron, run_turn))
