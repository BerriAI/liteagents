"""Worked example: structured output via a single forced tool call.

Instead of asking the model to produce free-text (and parsing it), we give
it exactly one tool and force the call via tool_choice, then read the
result straight off the ToolUseBlock. No JSON parsing, no retries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .agent import LiteAgentClient, LiteAgentOptions
from .routers.base import ModelRouter
from .tools import Tool
from .types import AssistantMessage, ToolUseBlock


@dataclass
class PullRequest:
    title: str
    diff: str


@dataclass
class RiskAssessment:
    risk: Literal["low", "medium", "high"]
    reasons: list[str]
    recommended_checks: list[str]


class _EmitAssessmentTool(Tool):
    name = "emit_assessment"
    description = (
        "Emit the final risk assessment for this pull request. Call this "
        "exactly once, as your only tool call."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "risk": {"type": "string", "enum": ["low", "medium", "high"]},
            "reasons": {"type": "array", "items": {"type": "string"}},
            "recommended_checks": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["risk", "reasons", "recommended_checks"],
    }

    async def execute(self, input: dict[str, Any]) -> str:
        # PRRiskAgent.classify() reads `input` directly off the ToolUseBlock
        # rather than parsing this return value.
        return "ok"


class PRRiskAgent:
    """Classifies a pull request's deployment risk in one model call, using
    a forced tool call for structured output instead of free-text parsing."""

    def __init__(self, *, model: str | None = None, model_router: ModelRouter | None = None) -> None:
        self._options = LiteAgentOptions(
            model=model,
            model_router=model_router,
            tools=[_EmitAssessmentTool()],
            system=(
                "You are a senior reviewer assessing pull request risk. "
                "Call emit_assessment exactly once with your verdict."
            ),
            # One model call is all we need: tool_choice forces the
            # emit_assessment call, and classify() reads its input off the
            # first AssistantMessage without waiting for a second round.
            max_turns=1,
            tool_choice={"type": "tool", "name": "emit_assessment"},
        )

    async def classify(self, pr: PullRequest) -> RiskAssessment:
        captured: dict[str, Any] = {}
        async with LiteAgentClient(options=self._options) as agent:
            async for message in agent.query(f"Title: {pr.title}\n\nDiff:\n{pr.diff}"):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, ToolUseBlock) and block.name == "emit_assessment":
                            captured.update(block.input)

        if not captured:
            raise RuntimeError("Model did not emit a risk assessment.")
        return RiskAssessment(
            risk=captured["risk"],
            reasons=captured["reasons"],
            recommended_checks=captured["recommended_checks"],
        )
