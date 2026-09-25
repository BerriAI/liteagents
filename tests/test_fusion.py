from __future__ import annotations

from liteagents.legacy import (
    FusionOptions,
    LiteAgentClient,
    LiteAgentOptions,
    ToolResultBlock,
    UserMessage,
)

from .conftest import text_response, tool_use_response


async def test_delegate_to_sidekick_uses_separate_history(mock_anthropic_messages):
    mock_anthropic_messages.push(
        tool_use_response(
            tool_use_id="tu_deleg",
            name="delegate_to_sidekick",
            input={"task": "run the tests"},
            model="anthropic/claude-opus-4-8",
        )
    )
    mock_anthropic_messages.push(text_response("142 passed, 0 failed", model="openai/gpt-5.4-mini"))
    mock_anthropic_messages.push(text_response("all good", model="anthropic/claude-opus-4-8"))

    options = LiteAgentOptions(
        model="anthropic/claude-opus-4-8",
        fusion=FusionOptions(sidekick_model="openai/gpt-5.4-mini"),
    )

    async with LiteAgentClient(options=options) as agent:
        messages = [m async for m in agent.query("modernize search.js and run tests")]

    tool_result_messages = [
        m for m in messages if isinstance(m, UserMessage) and isinstance(m.content, list)
        and any(isinstance(b, ToolResultBlock) for b in m.content)
    ]
    assert len(tool_result_messages) == 1
    assert "142 passed" in tool_result_messages[0].content[0].content

    main_history_models = {call["model"] for call in mock_anthropic_messages.calls[:1] + mock_anthropic_messages.calls[2:]}
    assert main_history_models == {"anthropic/claude-opus-4-8"}
    assert mock_anthropic_messages.calls[1]["model"] == "openai/gpt-5.4-mini"

    sidekick_history = agent._fusion._sidekick_history.raw()  # type: ignore[attr-defined]
    assert sidekick_history[0]["content"] == "run the tests"


async def test_second_delegation_reuses_sidekick_history(mock_anthropic_messages):
    mock_anthropic_messages.push(
        tool_use_response(
            tool_use_id="tu_1", name="delegate_to_sidekick", input={"task": "first task"},
            model="anthropic/claude-opus-4-8",
        )
    )
    mock_anthropic_messages.push(text_response("first done", model="openai/gpt-5.4-mini"))
    mock_anthropic_messages.push(text_response("ack 1", model="anthropic/claude-opus-4-8"))
    mock_anthropic_messages.push(
        tool_use_response(
            tool_use_id="tu_2", name="delegate_to_sidekick", input={"task": "second task"},
            model="anthropic/claude-opus-4-8",
        )
    )
    mock_anthropic_messages.push(text_response("second done", model="openai/gpt-5.4-mini"))
    mock_anthropic_messages.push(text_response("ack 2", model="anthropic/claude-opus-4-8"))

    options = LiteAgentOptions(
        model="anthropic/claude-opus-4-8",
        fusion=FusionOptions(sidekick_model="openai/gpt-5.4-mini"),
    )

    async with LiteAgentClient(options=options) as agent:
        [m async for m in agent.query("turn one")]
        [m async for m in agent.query("turn two")]

        sidekick_raw = agent._fusion._sidekick_history.raw()  # type: ignore[attr-defined]

    user_entries = [entry for entry in sidekick_raw if entry["role"] == "user"]
    assert len(user_entries) == 2
    assert user_entries[0]["content"] == "first task"
    assert user_entries[1]["content"] == "second task"
