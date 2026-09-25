from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._internal.compaction_runtime import make_compaction_runtime
from .compaction import CompactionOptions
from .compaction.background import BackgroundMemoryOptions
from .history import ConversationHistory
from .loop import run_tool_loop
from .routers.base import StaticRouter
from .tools import Tool
from .types import AssistantMessage, TextBlock


@dataclass
class FusionOptions:
    """Opt-in: run a frontier model with a cheaper sidekick model, delegating
    well-scoped subtasks to the sidekick instead of paying the frontier
    model's price/latency for mechanical work."""

    sidekick_model: str
    sidekick_max_turns: int = 10
    sidekick_max_tokens: int = 4096
    sidekick_compaction: CompactionOptions | BackgroundMemoryOptions | None = None


class DelegateToSidekickTool(Tool):
    name = "delegate_to_sidekick"
    description = (
        "Delegate a well-scoped, self-contained subtask to a cheaper sidekick "
        "model. Use for mechanical work (running tests, applying a known "
        "refactor, fetching or summarizing a file) that doesn't need your "
        "judgment. The sidekick has the same tools you do and keeps its own "
        "conversation history across delegations in this session."
    )
    input_schema = {  # noqa: RUF012 - Tool supports class or instance schemas
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": (
                    "The subtask, described in enough detail for an "
                    "independent model to execute it without further "
                    "clarification."
                ),
            }
        },
        "required": ["task"],
    }

    def __init__(self, runtime: FusionRuntime) -> None:
        self._runtime = runtime

    async def execute(self, input: dict[str, Any]) -> str:
        return await self._runtime.delegate(input["task"])


class FusionRuntime:
    """Owns the sidekick's own persistent ConversationHistory and router, kept
    separate from the main agent's history so the sidekick's context stays
    independently cached across delegations within one session -- this is
    what makes delegation NOT cost a cache miss the way calling another
    model as a plain tool would (re-sending full task context every call).
    """

    def __init__(self, *, main_tools: list[Tool], options: FusionOptions,
                 model_kwargs: dict[str, Any] | None = None) -> None:
        self._sidekick_tools = list(main_tools)  # sidekick can use the same real tools
        self._sidekick_router = StaticRouter(options.sidekick_model)
        self._sidekick_history = ConversationHistory()
        self._options = options
        self._model_kwargs = dict(model_kwargs or {})
        self._delegate_tool = DelegateToSidekickTool(self)
        self._delegation_count = 0
        self._compaction = make_compaction_runtime(options.sidekick_compaction)

    async def close(self) -> None:
        if self._compaction is not None:
            await self._compaction.cancel_pending()

    def tools_for_main_loop(self) -> list[Tool]:
        return [*self._sidekick_tools, self._delegate_tool]

    async def delegate(self, task: str) -> str:
        """Runs a NESTED tool loop against the sidekick's own history and
        returns its final text as a plain string -- this becomes the
        tool_result content the main model sees.

        Awaited inline, sequentially, on the same event loop as the caller.
        No subprocess/thread/task group -- delegations happen one at a time
        because the main loop is itself blocked waiting on this tool_use
        round, so there is nothing to run concurrently against; adding
        concurrency machinery here would just be unused complexity for v1.
        """
        self._delegation_count += 1
        self._sidekick_history.add_user_text(task)

        last_assistant: AssistantMessage | None = None
        async for message in run_tool_loop(
            history=self._sidekick_history,
            router=self._sidekick_router,
            tools=self._sidekick_tools,
            system=None,
            max_tokens=self._options.sidekick_max_tokens,
            max_turns=self._options.sidekick_max_turns,
            turn_index=self._delegation_count,
            prompt_for_router=task,
            model_kwargs=self._model_kwargs,
            compaction=self._compaction,
        ):
            if isinstance(message, AssistantMessage):
                last_assistant = message

        if last_assistant is None:
            return "Error: sidekick produced no response."
        text = "\n".join(b.text for b in last_assistant.content if isinstance(b, TextBlock))
        return text or "(sidekick produced no text)"
