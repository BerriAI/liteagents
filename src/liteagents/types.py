"""Public message and content-block types.

Plain dataclasses, mirroring claude_agent_sdk's public types field-for-field
where the concepts overlap. These are the only shapes a caller of liteagents
ever needs to import — raw Anthropic-wire dicts stay internal to
ConversationHistory (see history.py) and never appear in a public signature.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeAlias


@dataclass
class TextBlock:
    text: str


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: str | list[dict[str, Any]] | None = None
    is_error: bool | None = None


ContentBlock: TypeAlias = TextBlock | ToolUseBlock | ToolResultBlock


@dataclass
class UserMessage:
    content: str | list[ContentBlock]


@dataclass
class AssistantMessage:
    content: list[ContentBlock]
    model: str
    stop_reason: str | None = None
    usage: dict[str, Any] | None = None


Message: TypeAlias = UserMessage | AssistantMessage


@dataclass
class TurnContext:
    """What a ModelRouter sees before picking a model for one round.

    `history` is the typed message view (list[Message]), never raw wire
    dicts -- a router should be able to make routing decisions by reading
    the same shapes a caller of query()/LiteAgentClient sees.
    """

    prompt: str
    history: list[Message]
    turn: int
