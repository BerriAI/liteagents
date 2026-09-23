"""Reduce older tool outputs without calling a model."""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..types import CompactionUpdate, ReplaceToolResult, ToolResultBlock, UserMessage
from .base import CompactionContext, CompactionResult


@dataclass(frozen=True)
class PruneToolResults:
    """Replace older tool outputs with a marker, retaining calls, IDs, and errors."""

    keep: int = 3
    placeholder: str = "[Earlier tool output removed to reduce context.]"

    def __post_init__(self) -> None:
        if self.keep < 0 or not self.placeholder:
            raise ValueError("keep must be nonnegative and placeholder must not be empty")

    async def compact(self, context: CompactionContext) -> CompactionResult | None:
        results = [(index, block) for index, message in enumerate(context.messages)
                   if isinstance(message, UserMessage) and not isinstance(message.content, str)
                   for block in message.content if isinstance(block, ToolResultBlock)]
        eligible = results[:-self.keep] if self.keep else results
        edits = tuple(
            ReplaceToolResult(index, block.tool_use_id, self.placeholder)
            for index, block in eligible
            if block.content != self.placeholder and context.token_counter(
                context.model, json.dumps(block.content, ensure_ascii=False)
            ).tokens > context.token_counter(
                context.model, json.dumps(self.placeholder, ensure_ascii=False)
            ).tokens
        )
        if not edits:
            return None
        return CompactionResult(CompactionUpdate(len(context.messages), tool_results=edits),
                                state=context.state)
