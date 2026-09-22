from __future__ import annotations

from liteagents import AssistantMessage, LiteAgentOptions, Tool, ToolResultBlock, UserMessage, query

from .conftest import text_response, tool_use_response


class EchoTool(Tool):
    name = "echo"
    description = "Echoes its input."
    input_schema = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}

    async def execute(self, input):
        return f"echoed: {input['text']}"


async def test_single_turn_text_response(mock_anthropic_messages):
    mock_anthropic_messages.push(text_response("hi", model="openai/gpt-5.4-mini"))
    options = LiteAgentOptions(model="openai/gpt-5.4-mini")

    messages = [m async for m in query(prompt="hello", options=options)]

    assert len(messages) == 1
    assert isinstance(messages[0], AssistantMessage)
    assert messages[0].content[0].text == "hi"
    assert messages[0].stop_reason == "end_turn"


async def test_tool_use_round_trip(mock_anthropic_messages):
    mock_anthropic_messages.push(
        tool_use_response(tool_use_id="tu_1", name="echo", input={"text": "hi"}, model="openai/gpt-5.4-mini")
    )
    mock_anthropic_messages.push(text_response("done", model="openai/gpt-5.4-mini"))
    options = LiteAgentOptions(model="openai/gpt-5.4-mini", tools=[EchoTool()])

    messages = [m async for m in query(prompt="use the tool", options=options)]

    assert len(messages) == 3
    assert isinstance(messages[1], UserMessage)
    result_block = messages[1].content[0]
    assert isinstance(result_block, ToolResultBlock)
    assert result_block.content == "echoed: hi"
    assert result_block.is_error is False

    second_call_messages = mock_anthropic_messages.calls[1]["messages"]
    assert second_call_messages[-1]["content"][0]["type"] == "tool_result"


async def test_unknown_tool_returns_error_result(mock_anthropic_messages):
    mock_anthropic_messages.push(
        tool_use_response(tool_use_id="tu_1", name="nonexistent", input={}, model="openai/gpt-5.4-mini")
    )
    mock_anthropic_messages.push(text_response("done", model="openai/gpt-5.4-mini"))
    options = LiteAgentOptions(model="openai/gpt-5.4-mini", tools=[])

    messages = [m async for m in query(prompt="use a tool", options=options)]

    result_block = messages[1].content[0]
    assert isinstance(result_block, ToolResultBlock)
    assert result_block.is_error is True
    assert "no tool named" in result_block.content


async def test_max_turns_stops_loop(mock_anthropic_messages):
    for _ in range(3):
        mock_anthropic_messages.push(
            tool_use_response(tool_use_id="tu_1", name="echo", input={"text": "x"}, model="openai/gpt-5.4-mini")
        )
    options = LiteAgentOptions(model="openai/gpt-5.4-mini", tools=[EchoTool()], max_turns=3)

    messages = [m async for m in query(prompt="loop forever", options=options)]

    assert len(mock_anthropic_messages.calls) == 3
    assert messages[-2].stop_reason == "tool_use"
