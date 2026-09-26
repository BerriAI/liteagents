"""Native per-run metadata must not bypass the recorded operation results."""

from uuid import uuid4

import pytest

from liteagents import AssistantMessage, ProfileOptions, TextBlock
from liteagents.runtime.control import CURRENT, RunControl, stable
from liteagents.storage import RunStore
from tests.test_python_harnesses import Lookup


def test_conversation_metadata_is_ignored_but_application_ids_are_preserved():
    payload = {
        "run_id": "native-run", "conversation_id": "native-conversation",
        "parts": [{
            "part_kind": "tool-return", "timestamp": "native-time",
            "content": {"conversation_id": "customer-conversation", "run_id": "customer-run"},
        }],
    }
    assert stable([payload]) == [{"parts": [{
        "part_kind": "tool-return",
        "content": {"conversation_id": "customer-conversation", "run_id": "customer-run"},
    }]}]


@pytest.mark.integration
async def test_fresh_pydantic_loop_replays_models_and_tools(tmp_path):
    pytest.importorskip("pydantic_ai")
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
    from pydantic_ai.models.function import FunctionModel

    from liteagents.harnesses.pydantic_ai import PydanticAIAdapter

    requests = []

    def respond(messages, info):
        requests.append(messages)
        if any(isinstance(p, ToolReturnPart) for m in messages for p in m.parts):
            return ModelResponse(parts=[TextPart("Order total: USD 12")])
        return ModelResponse(parts=[ToolCallPart("lookup", {"order": "A123"}, uuid4().hex)])

    store = RunStore("sqlite:///" + str(tmp_path / "state.sqlite"))
    await store.setup()
    await store.ensure_run("same-job", "v1")
    tool = Lookup()
    for _ in range(2):
        profile = ProfileOptions(
            harness="pydantic-ai", model="scripted/test",
            harness_options={"model_instance": FunctionModel(respond)},
        )
        adapter = PydanticAIAdapter(profile, cwd=tmp_path, tools=[tool], session_id=uuid4().hex)
        token = CURRENT.set(RunControl(profile, run_key="same-job", store=store))
        try:
            await adapter.open()
            messages = [m async for m in adapter.query("Look up order A123", run_id="same-job")]
            assert any(isinstance(m, AssistantMessage) and TextBlock("Order total: USD 12")
                       in m.content for m in messages)
        finally:
            await adapter.close()
            CURRENT.reset(token)
    assert len(requests) == 2  # Both model results replay in the second native loop.
    assert tool.calls == [{"order": "A123"}]
