"""A real worker subprocess used for crash injection through the public SDK."""

import asyncio
import sys
from pathlib import Path

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from liteagents import ProfileOptions, TemporalOptions
from liteagents.temporal import LiteAgentWorker
from tests.operation_fixture import FixtureTool


class Model(BaseChatModel):
    delegate: bool = False

    @property
    def _llm_type(self):
        return "crash-test"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        done = {m.name for m in messages if isinstance(m, ToolMessage)}
        sequence = ("delegate_auditor",) if self.delegate else ("lookup", "slow")
        name = next((name for name in sequence if name not in done), None)
        arguments = {"prompt": "Read and validate the order"} if self.delegate else {}
        message = (
            AIMessage(content="", tool_calls=[{"id": name, "name": name, "args": arguments}])
            if name
            else AIMessage(content="Validated USD 12")
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


async def main():
    cwd = Path(sys.argv[1])
    harness = sys.argv[3] if len(sys.argv) > 3 else "deepagents"
    mode = sys.argv[4] if len(sys.argv) > 4 else "tool"
    model = Model(delegate=mode == "child")
    child_model = Model()
    if harness == "pydantic-ai":
        from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
        from pydantic_ai.models.function import FunctionModel

        def respond(messages, info):
            done = {p.tool_name for m in messages for p in m.parts if isinstance(p, ToolReturnPart)}
            delegated = any(t.name == "delegate_auditor" for t in info.function_tools)
            sequence = ("delegate_auditor",) if delegated else ("lookup", "slow")
            name = next((n for n in sequence if n not in done), None)
            arguments = {"prompt": "Read and validate the order"} if delegated else {}
            return ModelResponse(
                parts=[ToolCallPart(name, arguments, name)]
                if name
                else [TextPart("Validated USD 12")]
            )

        model = FunctionModel(respond)
        child_model = model
    profile = ProfileOptions(
        harness=harness,
        model="scripted/test",
        tools=["lookup", "slow"],
        harness_options={"model_instance": model},
        temporal=TemporalOptions(
            profile_id=sys.argv[2],
            checkpoint_path=str(cwd / "checkpoints.sqlite"),
            heartbeat_timeout_seconds=3,
            activity_timeout_seconds=60,
        ),
    )
    tools = [FixtureTool(name, cwd / "calls.txt") for name in ("lookup", "slow")]
    if mode == "approval":
        profile.harness_options["interrupt_on"] = {"slow": True}
    if mode == "child":
        from liteagents.profiles import SubagentOptions

        profile.tools = ["read_file"]
        profile.features.subagents = True
        profile.subagents = {
            "auditor": SubagentOptions(description="Audit order", tools=["lookup", "slow"])
        }
        profile.harness_options["subagent_model_instances"] = {"auditor": child_model}
    await LiteAgentWorker(profile=profile, tools=tools, cwd=cwd).run()


if __name__ == "__main__":
    asyncio.run(main())
