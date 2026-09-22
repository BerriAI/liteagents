from __future__ import annotations

import pytest

from liteagents import PRRiskAgent, PullRequest

from .conftest import tool_use_response


async def test_classify_returns_structured_assessment(mock_anthropic_messages):
    mock_anthropic_messages.push(
        tool_use_response(
            tool_use_id="tu_risk",
            name="emit_assessment",
            input={
                "risk": "high",
                "reasons": ["touches auth", "no tests added"],
                "recommended_checks": ["run full auth test suite"],
            },
            model="anthropic/claude-opus-4-8",
        )
    )
    agent = PRRiskAgent(model="anthropic/claude-opus-4-8")

    assessment = await agent.classify(PullRequest(title="Add API key rotation", diff="--- a/auth.py"))

    assert assessment.risk == "high"
    assert assessment.reasons == ["touches auth", "no tests added"]
    assert assessment.recommended_checks == ["run full auth test suite"]

    call = mock_anthropic_messages.calls[0]
    assert call["tool_choice"] == {"type": "tool", "name": "emit_assessment"}


async def test_classify_raises_when_model_never_emits(mock_anthropic_messages):
    from .conftest import text_response

    mock_anthropic_messages.push(text_response("I don't know", model="anthropic/claude-opus-4-8"))
    agent = PRRiskAgent(model="anthropic/claude-opus-4-8")

    with pytest.raises(RuntimeError):
        await agent.classify(PullRequest(title="x", diff="y"))
