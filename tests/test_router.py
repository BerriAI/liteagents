from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

from liteagents import LiteAgentOptions, TurnContext, query

from .conftest import text_response


@dataclass
class RecordingRouter:
    model: str
    contexts: list[TurnContext] = field(default_factory=list)

    async def route(self, context: TurnContext) -> str:
        self.contexts.append(context)
        return self.model


async def test_router_called_with_correct_context(mock_anthropic_messages):
    router = RecordingRouter(model="anthropic/claude-sonnet-4-6")
    mock_anthropic_messages.push(text_response("ok", model="anthropic/claude-sonnet-4-6"))
    options = LiteAgentOptions(model_router=router)

    [m async for m in query(prompt="review the architecture", options=options)]

    assert len(router.contexts) == 1
    assert router.contexts[0].prompt == "review the architecture"
    assert router.contexts[0].turn == 1
    # the prompt is already on history by the time the router sees it --
    # LiteAgentClient.query() pushes it before calling the loop. The context
    # is a snapshot, so it stays at 1 entry even though history grows to 2
    # once the (later) assistant reply is appended.
    assert len(router.contexts[0].history) == 1
    assert router.contexts[0].history[0].content == "review the architecture"
    assert mock_anthropic_messages.calls[0]["model"] == "anthropic/claude-sonnet-4-6"


async def test_router_reselects_each_round(mock_anthropic_messages):
    from liteagents import Tool

    from .conftest import tool_use_response

    class NoopTool(Tool):
        name = "noop"
        description = "Does nothing."
        input_schema: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

        async def execute(self, input):
            return "ok"

    router = RecordingRouter(model="openai/gpt-5.4-mini")
    mock_anthropic_messages.push(
        tool_use_response(tool_use_id="tu_1", name="noop", input={}, model="openai/gpt-5.4-mini")
    )
    mock_anthropic_messages.push(text_response("done", model="openai/gpt-5.4-mini"))
    options = LiteAgentOptions(model_router=router, tools=[NoopTool()])

    [m async for m in query(prompt="go", options=options)]

    assert len(router.contexts) == 2
    assert router.contexts[0].turn == router.contexts[1].turn == 1
