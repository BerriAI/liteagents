"""A real worker subprocess used for crash injection through the public SDK."""

import asyncio
import sys
from pathlib import Path

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from liteagents import ProfileOptions, TemporalOptions, Tool
from liteagents.temporal import LiteAgentWorker


class Model(BaseChatModel):
    @property
    def _llm_type(self):
        return "crash-test"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        done = {m.name for m in messages if isinstance(m, ToolMessage)}
        name = next((name for name in ("lookup", "slow") if name not in done), None)
        message = (
            AIMessage(content="", tool_calls=[{"id": name, "name": name, "args": {}}])
            if name
            else AIMessage(content="Validated USD 12")
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


class FixtureTool(Tool):
    def __init__(self, name, marker):
        self.name = name
        self.marker = marker
        self.description = name
        self.input_schema = {"type": "object", "properties": {}}

    def mark(self, text):
        with self.marker.open("a") as stream:
            stream.write(text + "\n")

    async def execute(self, input):
        if self.name == "lookup":
            self.mark("lookup")
            return "USD 12"
        self.mark("slow-start")
        await asyncio.sleep(6)
        self.mark("slow-done")
        return "Validated USD 12"


async def main():
    cwd = Path(sys.argv[1])
    profile = ProfileOptions(
        harness="deepagents",
        model="scripted/test",
        tools=["lookup", "slow"],
        harness_options={"model_instance": Model()},
        temporal=TemporalOptions(
            profile_id=sys.argv[2],
            checkpoint_path=str(cwd / "checkpoints.sqlite"),
            heartbeat_timeout_seconds=3,
            activity_timeout_seconds=60,
        ),
    )
    tools = [FixtureTool(name, cwd / "calls.txt") for name in ("lookup", "slow")]
    await LiteAgentWorker(profile=profile, tools=tools, cwd=cwd).run()


if __name__ == "__main__":
    asyncio.run(main())
