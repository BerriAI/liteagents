"""Restore application tool identities without changing native tool-call IDs."""

from dataclasses import replace

from ..types import AgentEvent, AssistantMessage, ToolUseBlock


def normalize_tool_names(event: AgentEvent, names: dict[str, str]) -> AgentEvent:
    if not isinstance(event, AssistantMessage):
        return event
    blocks = [
        replace(block, name=names[block.name], native_name=block.native_name or block.name)
        if isinstance(block, ToolUseBlock) and block.name in names
        else block
        for block in event.content
    ]
    # Native adapters can attach turn usage after yielding this message. Keep
    # that identity so the completed RunResult receives the final usage report.
    event.content = blocks
    return event
