"""Provider-independent agent SDK built on LiteLLM.

Every name below is what the README's examples import -- keep this list in
sync with the README rather than the other way around.
"""

from __future__ import annotations

from .agent import LiteAgentClient, LiteAgentOptions, query
from .compaction import (
    CompactionContext,
    CompactionError,
    CompactionOptions,
    CompactionResult,
    CompactionStrategy,
    CompactionTrigger,
    ContextBudgetExceeded,
    PruneToolResults,
    RecentTokens,
    Summarize,
    TokenCounter,
    TokenEstimate,
    TokenThreshold,
)
from .fusion import FusionOptions
from .history import apply_compaction
from .pr_risk_agent import PRRiskAgent, PullRequest, RiskAssessment
from .routers import JevAgent, JevModelRouter, JevTier, ModelRouter, StaticRouter
from .tools import Tool
from .types import (
    AgentEvent,
    AssistantMessage,
    CompactionCompleted,
    CompactionEvent,
    CompactionFailed,
    CompactionReason,
    CompactionSkipped,
    CompactionStarted,
    CompactionUpdate,
    ContentBlock,
    Message,
    ReplacePrefix,
    ReplaceToolResult,
    SummaryMessage,
    TextBlock,
    TextDelta,
    ToolResultBlock,
    ToolUseBlock,
    TurnContext,
    UserMessage,
)

__all__ = [
    "AgentEvent",
    "AssistantMessage",
    "CompactionCompleted",
    "CompactionContext",
    "CompactionError",
    "CompactionEvent",
    "CompactionFailed",
    "CompactionOptions",
    "CompactionReason",
    "CompactionResult",
    "CompactionSkipped",
    "CompactionStarted",
    "CompactionStrategy",
    "CompactionTrigger",
    "CompactionUpdate",
    "ContentBlock",
    "ContextBudgetExceeded",
    "FusionOptions",
    "JevAgent",
    "JevModelRouter",
    "JevTier",
    "LiteAgentClient",
    "LiteAgentOptions",
    "Message",
    "ModelRouter",
    "PRRiskAgent",
    "PruneToolResults",
    "PullRequest",
    "RecentTokens",
    "ReplacePrefix",
    "ReplaceToolResult",
    "RiskAssessment",
    "StaticRouter",
    "Summarize",
    "SummaryMessage",
    "TextBlock",
    "TextDelta",
    "TokenCounter",
    "TokenEstimate",
    "TokenThreshold",
    "Tool",
    "ToolResultBlock",
    "ToolUseBlock",
    "TurnContext",
    "UserMessage",
    "apply_compaction",
    "query",
]
