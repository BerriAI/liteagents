"""Optional v1 compatibility API; install liteagents[legacy]."""

from __future__ import annotations

from ..errors import (
    ConfigurationError,
    HarnessError,
    MissingDependencyError,
    RunAlreadyExistsError,
    RunNotFoundError,
    UnsupportedFeatureError,
)
from ..harnesses import available_harnesses, get_capabilities
from ..profiles import FeatureOptions, ProfileOptions, RecoveryOptions, TemporalOptions
from ..runs import RunResult
from ..tools import Tool
from ..types import (
    AgentEvent,
    AssistantMessage,
    ContentBlock,
    Message,
    TextBlock,
    TextDelta,
    ToolResultBlock,
    ToolUseBlock,
    TurnContext,
    UserMessage,
)
from .agent import LiteAgentClient, LiteAgentOptions, query
from .fusion import FusionOptions
from .pr_risk_agent import PRRiskAgent, PullRequest, RiskAssessment
from .routers import JevAgent, JevModelRouter, JevTier, ModelRouter, StaticRouter

__all__ = [
    "AgentEvent",
    "AssistantMessage",
    "ConfigurationError",
    "ContentBlock",
    "FeatureOptions",
    "FusionOptions",
    "HarnessError",
    "JevAgent",
    "JevModelRouter",
    "JevTier",
    "LiteAgentClient",
    "LiteAgentOptions",
    "Message",
    "MissingDependencyError",
    "ModelRouter",
    "PRRiskAgent",
    "ProfileOptions",
    "PullRequest",
    "RecoveryOptions",
    "RiskAssessment",
    "RunAlreadyExistsError",
    "RunNotFoundError",
    "RunResult",
    "StaticRouter",
    "TemporalOptions",
    "TextBlock",
    "TextDelta",
    "Tool",
    "ToolResultBlock",
    "ToolUseBlock",
    "TurnContext",
    "UnsupportedFeatureError",
    "UserMessage",
    "available_harnesses",
    "get_capabilities",
    "query",
]
