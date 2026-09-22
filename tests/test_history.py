from __future__ import annotations

from liteagents import AssistantMessage, TextBlock, ToolResultBlock, UserMessage
from liteagents.history import ConversationHistory


def test_add_user_text_updates_raw_and_messages():
    history = ConversationHistory()
    message = history.add_user_text("hello")

    assert isinstance(message, UserMessage)
    assert message.content == "hello"
    assert history.raw() == [{"role": "user", "content": "hello"}]
    assert history.messages == [message]


def test_add_assistant_response_round_trips_tool_use_verbatim():
    history = ConversationHistory()
    content_dicts = [{"type": "tool_use", "id": "tu_1", "name": "echo", "input": {"text": "hi"}}]

    message = history.add_assistant_response(content_dicts, model="openai/gpt-5.4-mini")

    assert isinstance(message, AssistantMessage)
    assert message.model == "openai/gpt-5.4-mini"
    assert history.raw()[-1] == {"role": "assistant", "content": content_dicts}


def test_add_user_tool_results_builds_tool_result_blocks():
    history = ConversationHistory()
    results = [{"type": "tool_result", "tool_use_id": "tu_1", "content": "echoed: hi", "is_error": False}]

    message = history.add_user_tool_results(results)

    assert isinstance(message, UserMessage)
    assert isinstance(message.content[0], ToolResultBlock)
    assert message.content[0].tool_use_id == "tu_1"
    assert message.content[0].content == "echoed: hi"
    assert history.raw()[-1] == {"role": "user", "content": results}


def test_text_block_round_trips():
    history = ConversationHistory()
    content_dicts = [{"type": "text", "text": "hi there"}]

    message = history.add_assistant_response(content_dicts, model="openai/gpt-5.4-mini")

    assert isinstance(message.content[0], TextBlock)
    assert message.content[0].text == "hi there"
