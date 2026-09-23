"""Public message and content-block types.

Plain dataclasses, mirroring claude_agent_sdk's public types field-for-field
where the concepts overlap. ConversationHistory owns the raw Anthropic-wire
history; custom token counters receive detached wire content in TokenCountRequest.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypeAlias


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
class SummaryMessage(UserMessage):
    """Generated context about earlier messages, serialized as a user message."""


@dataclass
class AssistantMessage:
    content: list[ContentBlock]
    model: str
    stop_reason: str | None = None
    usage: dict[str, Any] | None = None


Message: TypeAlias = UserMessage | AssistantMessage


@dataclass
class TextDelta:
    """Incremental display text; the completed AssistantMessage still follows."""

    text: str
    model: str


@dataclass(frozen=True)
class ReplacePrefix:
    """Replace messages before `stop` (exclusive) with a summary.

    The SDK also retains the latest human message if it falls in this prefix.
    Indices always refer to the original, uncompacted history.
    """

    stop: int
    summary: str


@dataclass(frozen=True)
class ReplaceToolResult:
    message_index: int
    tool_use_id: str
    content: str = "[Earlier tool output removed to reduce context.]"


@dataclass(frozen=True)
class CompactionUpdate:
    """Replayable edits against a history with `message_count` messages.

    A batch contains only `steps`; each step addresses the preceding candidate,
    not the original history. The entire batch is validated before committing.
    """

    message_count: int
    prefix: ReplacePrefix | None = None
    tool_results: tuple[ReplaceToolResult, ...] = ()
    steps: tuple[CompactionUpdate, ...] = ()


CompactionReason: TypeAlias = Literal["manual", "threshold", "budget"]


@dataclass(frozen=True)
class CompactionStarted:
    reason: CompactionReason
    model: str
    tokens_before: int
    token_source: str


@dataclass(frozen=True)
class CompactionCompleted:
    reason: CompactionReason
    model: str
    tokens_before: int
    tokens_after: int
    token_source: str
    update: CompactionUpdate
    usage: dict[str, Any] | None = None
    token_source_after: str | None = None


@dataclass(frozen=True)
class CompactionSkipped:
    reason: CompactionReason
    model: str
    detail: str
    usage: dict[str, Any] | None = None


@dataclass(frozen=True)
class CompactionFailed:
    reason: CompactionReason
    model: str
    error: str
    usage: dict[str, Any] | None = None


CompactionEvent: TypeAlias = (
    CompactionStarted | CompactionCompleted | CompactionSkipped | CompactionFailed
)
AgentEvent: TypeAlias = Message | TextDelta | CompactionEvent


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
