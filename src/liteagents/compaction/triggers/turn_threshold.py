"""Trigger on retained human turns, excluding summaries and tool-result messages."""

from dataclasses import dataclass

from ...types import SummaryMessage, ToolResultBlock, UserMessage
from ..base import CompactionContext


@dataclass(frozen=True)
class TurnThreshold:
    turns: int

    def __post_init__(self) -> None:
        if self.turns < 1:
            raise ValueError("turns must be positive")

    def should_compact(self, context: CompactionContext) -> bool:
        turns = sum(
            isinstance(message, UserMessage) and not isinstance(message, SummaryMessage)
            and (isinstance(message.content, str) or any(
                not isinstance(block, ToolResultBlock) for block in message.content
            ))
            for message in context.messages
        )
        return turns >= self.turns
