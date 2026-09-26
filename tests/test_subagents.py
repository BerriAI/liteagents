import asyncio

import pytest

from liteagents import LiteAgentClient, LiteAgentOptions, ProfileOptions
from tests.test_python_harnesses import Lookup, deep_model, pydantic_model


def parent_model(harness):
    pytest.importorskip("deepagents" if harness == "deepagents" else "pydantic_ai")
    if harness == "deepagents":
        from langchain_core.messages import AIMessage, ToolMessage
        from langchain_core.outputs import ChatGeneration, ChatResult

        class Parent(type(deep_model())):
            def bind_tools(self, tools, **kwargs):
                assert {t.name for t in tools} == {"read_file", "delegate_auditor"}
                return self

            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                returned = next((m for m in messages if isinstance(m, ToolMessage)), None)
                reply = (
                    AIMessage(content=returned.content)
                    if returned
                    else AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "call1",
                                "name": "delegate_auditor",
                                "args": {"prompt": "Look up A123"},
                            }
                        ],
                    )
                )
                return ChatResult(generations=[ChatGeneration(message=reply)])

        return Parent()
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
    from pydantic_ai.models.function import FunctionModel

    def respond(messages, info):
        assert {t.name for t in info.function_tools} == {"read_file", "delegate_auditor"}
        returned = next(
            (p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)), None
        )
        return ModelResponse(
            parts=[TextPart(returned.content)]
            if returned
            else [ToolCallPart("delegate_auditor", {"prompt": "Look up A123"}, "call1")]
        )

    return FunctionModel(respond)


@pytest.mark.parametrize(
    "harness,child", [("deepagents", deep_model), ("pydantic-ai", pydantic_model)]
)
async def test_named_child_model_tools_attribution_and_journal_scope(harness, child, tmp_path):
    tool = Lookup()
    profile = ProfileOptions(
        harness=harness,
        model="scripted/parent",
        tools=["read_file"],
        subagents={
            "auditor": {
                "description": "Audit an order",
                "model": "scripted/child",
                "tools": ["lookup"],
            }
        },
        harness_options={
            "model_instance": parent_model(harness),
            "subagent_model_instances": {"auditor": child()},
        },
    )
    async with LiteAgentClient(
        options=LiteAgentOptions(profile=profile, tools=[tool], cwd=tmp_path)
    ) as client:
        run = await client.start_run("Delegate the order audit")
        assert (await asyncio.wait_for(run.result(), 5)).text == "Order total: USD 12"
        assert len(tool.calls) == 1
        events = [e async for e in run.events()]
        started = next(e for e in events if e.kind == "subagent_started")
        assert started.data["agent"] == "auditor"
        assert started.data["model"] == "scripted/child"
        assert started.data["scope"] == "root/tool:call1"
        assert any(e.kind == "subagent_completed" for e in events)
        assert any(k.startswith("result:root/tool:call1:tool:call1") for k in run.control.values)
