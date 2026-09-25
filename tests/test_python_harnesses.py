from contextlib import aclosing
from typing import ClassVar

import pytest

from liteagents import (
    AssistantMessage,
    LiteAgentClient,
    LiteAgentOptions,
    ProfileOptions,
    RunAlreadyExistsError,
    TextBlock,
    Tool,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)


class Lookup(Tool):
    name = "lookup"
    description = "Return the order total."
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {"order": {"type": "string"}},
        "required": ["order"],
    }

    def __init__(self):
        self.calls = []

    async def execute(self, input):
        self.calls.append(input)
        return "USD 12"


def deep_model():
    pytest.importorskip("deepagents")
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class Model(BaseChatModel):
        @property
        def _llm_type(self):
            return "scripted"

        def bind_tools(self, tools, **kwargs):
            assert [t.name for t in tools] == ["lookup"]
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if any(isinstance(m, ToolMessage) for m in messages):
                content = AIMessage(content="Order total: USD 12")
            else:
                content = AIMessage(
                    content="",
                    tool_calls=[{"id": "call1", "name": "lookup", "args": {"order": "A123"}}],
                )
            return ChatResult(generations=[ChatGeneration(message=content)])

    return Model()


def pydantic_model():
    pytest.importorskip("pydantic_ai")
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
    from pydantic_ai.models.function import FunctionModel

    def respond(messages, info):
        if any(isinstance(part, ToolReturnPart) for m in messages for part in m.parts):
            return ModelResponse(parts=[TextPart("Order total: USD 12")])
        return ModelResponse(parts=[ToolCallPart("lookup", {"order": "A123"}, "call1")])

    return FunctionModel(respond)


@pytest.mark.integration
@pytest.mark.parametrize(
    "harness,factory", [("deepagents", deep_model), ("pydantic-ai", pydantic_model)]
)
async def test_real_harness_tool_loop_and_history(harness, factory, tmp_path):
    tool = Lookup()
    profile = ProfileOptions(
        harness=harness,
        model="scripted/test",
        tools=["lookup"],
        harness_options={"model_instance": factory()},
    )
    async with LiteAgentClient(
        options=LiteAgentOptions(profile=profile, tools=[tool], cwd=tmp_path)
    ) as client:
        events = [event async for event in client.query("Look up order A123", run_id="first")]
        assert tool.calls == [{"order": "A123"}]
        assert any(
            isinstance(e, AssistantMessage) and any(isinstance(b, ToolUseBlock) for b in e.content)
            for e in events
        )
        assert any(
            isinstance(e, UserMessage) and any(isinstance(b, ToolResultBlock) for b in e.content)
            for e in events
        )
        result = await (await client.get_run("first")).result()
        assert result.text == "Order total: USD 12"
        assert result.harness == harness
        following = [e async for e in client.query("Repeat the total", run_id="second")]
        assert tool.calls == [{"order": "A123"}]
        assert any(
            isinstance(e, AssistantMessage) and TextBlock("Order total: USD 12") in e.content
            for e in following
        )
        with pytest.raises(RunAlreadyExistsError):
            async with aclosing(client.query("duplicate", run_id="first")) as stream:
                await anext(stream)


@pytest.mark.parametrize(
    "field,value",
    [("features", {"subagents": True}), ("harness_options", {"typo": True})],
)
def test_unimplemented_features_fail_before_execution(field, value, tmp_path):
    from liteagents import UnsupportedFeatureError

    pytest.importorskip("deepagents")
    profile = ProfileOptions(**({"harness": "deepagents", "model": "openai/test"} | {field: value}))
    with pytest.raises(UnsupportedFeatureError):
        LiteAgentClient(options=LiteAgentOptions(profile=profile, cwd=tmp_path))
