"""Provider-independent agent SDK built on LiteLLM.

Every name below is what the README's examples import -- keep this list in
sync with the README rather than the other way around.
"""

from __future__ import annotations

from .agent import LiteAgentClient, LiteAgentOptions, query
from .errors import (
    ConfigurationError,
    HarnessError,
    MissingDependencyError,
    RunAlreadyExistsError,
    RunNotFoundError,
    UnsupportedFeatureError,
)
from .fusion import FusionOptions
from .harnesses import available_harnesses, get_capabilities
from .pr_risk_agent import PRRiskAgent, PullRequest, RiskAssessment
from .profiles import FeatureOptions, ProfileOptions, RecoveryOptions, TemporalOptions
from .routers import JevAgent, JevModelRouter, JevTier, ModelRouter, StaticRouter
from .runs import RunResult
from .tools import Tool
from .types import (
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
