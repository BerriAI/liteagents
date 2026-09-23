"""Public message and content-block types.

Plain dataclasses, mirroring claude_agent_sdk's public types field-for-field
where the concepts overlap. ConversationHistory owns the raw Anthropic-wire
history; custom token counters receive detached wire content in TokenCountRequest.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypeAlias, TypedDict

from typing_extensions import NotRequired

from ._internal.validation import integer, nonempty
from .usage import TokenUsage


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

    def __post_init__(self) -> None:
        integer(self.stop, "stop", minimum=1)
        nonempty(self.summary, "summary")


@dataclass(frozen=True)
class ReplaceToolResult:
    message_index: int
    tool_use_id: str
    content: str = "[Earlier tool output removed to reduce context.]"

    def __post_init__(self) -> None:
        integer(self.message_index, "message_index")
        nonempty(self.tool_use_id, "tool_use_id")
        if not isinstance(self.content, str):
            raise TypeError("replacement content must be a string")


@dataclass(frozen=True)
class HistoryEdit:
    """Direct edits against one history snapshot; indices refer to that snapshot."""

    message_count: int
    prefix: ReplacePrefix | None = None
    tool_results: tuple[ReplaceToolResult, ...] = ()

    def __post_init__(self) -> None:
        integer(self.message_count, "message_count")
        object.__setattr__(self, "tool_results", tuple(self.tool_results))


@dataclass(frozen=True)
class BatchUpdate:
    """Atomic sequence; each step addresses the preceding candidate history."""

    message_count: int
    steps: tuple[CompactionUpdate, ...]

    def __post_init__(self) -> None:
        integer(self.message_count, "message_count")
        object.__setattr__(self, "steps", tuple(self.steps))


CompactionUpdate: TypeAlias = HistoryEdit | BatchUpdate


@dataclass(frozen=True)
class TokenEstimate:
    tokens: int
    source: str

    def __post_init__(self) -> None:
        integer(self.tokens, "tokens")
        nonempty(self.source, "source")


class WireMessage(TypedDict):
    role: Literal["user", "assistant", "system"]
    content: str | list[dict[str, Any]]


class WireTool(TypedDict):
    name: str
    description: NotRequired[str]
    input_schema: dict[str, Any]


CompactionReason: TypeAlias = Literal["manual", "threshold", "budget"]


@dataclass(frozen=True)
class CompactionStarted:
    reason: CompactionReason
    model: str
    before: TokenEstimate


@dataclass(frozen=True)
class CompactionCompleted:
    reason: CompactionReason
    model: str
    before: TokenEstimate
    after: TokenEstimate
    update: CompactionUpdate
    usage: TokenUsage | None = None


@dataclass(frozen=True)
class CompactionSkipped:
    reason: CompactionReason
    model: str
    detail: str
    usage: TokenUsage | None = None


@dataclass(frozen=True)
class CompactionFailed:
    reason: CompactionReason
    model: str
    error: str
    usage: TokenUsage | None = None


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
