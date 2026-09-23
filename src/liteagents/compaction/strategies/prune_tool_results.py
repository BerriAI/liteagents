"""Reduce older tool outputs without calling a model."""

from __future__ import annotations

from dataclasses import dataclass

from ..._internal.validation import integer, nonempty
from ...types import HistoryEdit, ReplaceToolResult, ToolResultBlock, UserMessage
from ..base import CompactionContext, CompactionResult
from ..tokens import TokenCountRequest


@dataclass(frozen=True)
class PruneToolResults:
    """Replace older tool outputs with a marker, retaining calls, IDs, and errors."""

    keep: int = 3
    placeholder: str = "[Earlier tool output removed to reduce context.]"

    def __post_init__(self) -> None:
        integer(self.keep, "keep")
        nonempty(self.placeholder, "placeholder")

    async def compact(self, context: CompactionContext) -> CompactionResult | None:
        results = [(index, block) for index, message in enumerate(context.messages)
                   if isinstance(message, UserMessage) and not isinstance(message.content, str)
                   for block in message.content if isinstance(block, ToolResultBlock)]
        eligible = results[:-self.keep] if self.keep else results
        edits = tuple(
            ReplaceToolResult(index, block.tool_use_id, self.placeholder)
            for index, block in eligible
            if block.content != self.placeholder and context.count_tokens(
                TokenCountRequest(({"role": "user", "content": block.content or ""},))
            ).tokens > context.count_tokens(
                TokenCountRequest(({"role": "user", "content": self.placeholder},))
            ).tokens
        )
        if not edits:
            return None
        return CompactionResult(HistoryEdit(len(context.messages), tool_results=edits),
                                state=context.state)
