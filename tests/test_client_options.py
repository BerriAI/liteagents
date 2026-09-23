import pytest

from liteagents import (
    AssistantMessage,
    FusionOptions,
    LiteAgentClient,
    LiteAgentOptions,
    TextBlock,
    UserMessage,
    query,
)

from .conftest import text_response, tool_use_response


async def test_gateway_options_forwarded_to_every_round_and_sidekick(mock_anthropic_messages):
    mock_anthropic_messages.push(tool_use_response(tool_use_id="1", name="delegate_to_sidekick", input={"task": "check"}, model="main"))
    mock_anthropic_messages.push(text_response("checked", model="sidekick"))
    mock_anthropic_messages.push(text_response("done", model="main"))
    kwargs = {"api_base": "https://gateway.invalid/v1", "api_key": "SECRET", "num_retries": 0,
              "timeout": 90, "extra_headers": {"redact": "true"}, "extra_body": {"no-log": True}}
    options = LiteAgentOptions(model="main", fusion=FusionOptions(sidekick_model="sidekick"), model_kwargs=kwargs)
    assert "SECRET" not in repr(options)
    _ = [m async for m in query(prompt="check", options=options)]
    assert len(mock_anthropic_messages.calls) == 3
    for call in mock_anthropic_messages.calls:
        assert {key: call[key] for key in kwargs} == kwargs


async def test_typed_initial_history_is_copied_and_sent_to_model(mock_anthropic_messages):
    previous = [UserMessage("old question"), AssistantMessage([TextBlock("old answer")], model="old")]
    options = LiteAgentOptions(model="new")
    mock_anthropic_messages.push(text_response("new answer", model="new"))
    async with LiteAgentClient(options=options, history=previous) as client:
        previous[1].content[0].text = "changed"
        _ = [m async for m in client.query("follow up")]
        sent = mock_anthropic_messages.calls[0]["messages"]
        assert [m["role"] for m in sent] == ["user", "assistant", "user"]
        assert sent[1]["content"][0]["text"] == "old answer"
        assert client.history[1].content[0].text == "old answer"
    assert len(previous) == 2


@pytest.mark.parametrize("key", ["model", "messages", "system", "max_tokens", "tools", "tool_choice", "stream"])
def test_provider_kwargs_cannot_replace_loop_owned_fields(key):
    with pytest.raises(ValueError):
        LiteAgentOptions(model="test", model_kwargs={key: "override"})


def test_zero_turns_rejected():
    with pytest.raises(ValueError, match="max_turns"):
        LiteAgentOptions(model="test", max_turns=0)
