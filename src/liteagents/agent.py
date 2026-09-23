from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from contextlib import aclosing
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from typing_extensions import Self

from ._internal.compaction_runtime import CompactionRuntime
from .compaction import CompactionOptions
from .fusion import FusionOptions, FusionRuntime
from .history import ConversationHistory
from .loop import run_tool_loop
from .routers.base import ModelRouter, StaticRouter
from .tools import Tool
from .types import AgentEvent, CompactionCompleted, CompactionSkipped, Message


@dataclass
class LiteAgentOptions:
    model: str | None = None
    model_router: ModelRouter | None = None
    tools: list[Tool] = field(default_factory=list)
    system: str | None = None
    max_tokens: int = 4096
    max_turns: int = 20
    tool_choice: dict[str, Any] | None = None
    fusion: FusionOptions | None = None
    compaction: CompactionOptions | None = None
    stream: bool = False
    # Per-client connection/provider options, never process-global credentials.
    model_kwargs: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.model is None and self.model_router is None:
            raise ValueError("LiteAgentOptions requires model or model_router")
        reserved = {"model", "messages", "system", "max_tokens", "tools", "tool_choice", "stream"}
        if reserved.intersection(self.model_kwargs):
            raise ValueError("Use LiteAgentOptions fields for model, messages, tools and stream settings")
        if self.max_turns < 1:
            raise ValueError("max_turns must be at least 1")


class LiteAgentClient:
    """Stateful, multi-turn conversation. Owns the conversation history (and
    the fusion sidekick's separate history, if fusion is enabled) for the
    life of the `async with` block, so repeated .query() calls share cache.
    """

    def __init__(self, *, options: LiteAgentOptions, history: Sequence[Message] = ()) -> None:
        self._options = options
        self._router: ModelRouter = options.model_router or StaticRouter(options.model)  # type: ignore[arg-type]
        self._history = ConversationHistory(history)
        self._turn = 0
        self._fusion: FusionRuntime | None = None
        self._compaction = CompactionRuntime(options.compaction) if options.compaction else None
        self._busy = False

    async def __aenter__(self) -> Self:
        if self._options.fusion is not None:
            self._fusion = FusionRuntime(main_tools=self._options.tools, options=self._options.fusion,
                                         model_kwargs=self._options.model_kwargs)
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None  # no subprocess or connection to tear down; kept for API symmetry

    async def query(self, prompt: str) -> AsyncGenerator[AgentEvent, None]:
        if self._busy:
            raise RuntimeError("This client is already querying or compacting")
        self._busy = True
        try:
            async with aclosing(self._query(prompt)) as events:
                async for event in events:
                    yield event
        finally:
            self._busy = False

    async def _query(self, prompt: str) -> AsyncGenerator[AgentEvent, None]:
        self._turn += 1
        tools = self._fusion.tools_for_main_loop() if self._fusion else self._options.tools
        # run_tool_loop reads history.raw()/history.messages but never mutates
        # them with the incoming prompt itself -- the caller owns that write.
        self._history.add_user_text(prompt)
        async with aclosing(run_tool_loop(
            history=self._history,
            router=self._router,
            tools=tools,
            system=self._options.system,
            max_tokens=self._options.max_tokens,
            max_turns=self._options.max_turns,
            turn_index=self._turn,
            prompt_for_router=prompt,
            tool_choice=self._options.tool_choice,
            stream=self._options.stream,
            model_kwargs=self._options.model_kwargs,
            compaction=self._compaction,
        )) as events:
            async for message in events:
                yield message

    @property
    def history(self) -> list[Message]:
        """Detached copy of active model context, including any generated summaries."""
        return deepcopy(self._history.messages)

    async def compact(self, *, instructions: str | None = None,
                      model: str | None = None) -> CompactionCompleted | CompactionSkipped:
        """Compact while idle without routing or advancing the user turn counter.

        `model` selects the target context budget, not a summarizer override. A routed
        client uses its last selected model; before its first query, pass one explicitly.
        """
        if self._busy:
            raise RuntimeError("This client is already querying or compacting")
        if self._compaction is None:
            raise ValueError("Configure compaction before calling compact()")
        target = model or self._compaction.last_model
        if target is None and self._options.model_router is None:
            target = self._options.model
        if target is None:
            raise ValueError("Pass model= when compacting before the router's first selection")
        self._busy = True
        try:
            tools = self._fusion.tools_for_main_loop() if self._fusion else self._options.tools
            outcome: CompactionCompleted | CompactionSkipped | None = None
            async with aclosing(self._compaction.run(
                history=self._history, model=target, system=self._options.system, tools=tools,
                max_tokens=self._options.max_tokens, model_kwargs=self._options.model_kwargs,
                manual=True, instructions=instructions, tool_choice=self._options.tool_choice,
            )) as events:
                async for event in events:
                    if isinstance(event, (CompactionCompleted, CompactionSkipped)):
                        outcome = event
            assert outcome is not None
            return outcome
        finally:
            self._busy = False


async def query(*, prompt: str, options: LiteAgentOptions,
                history: Sequence[Message] = ()) -> AsyncGenerator[AgentEvent, None]:
    """Stateless single-prompt convenience wrapper -- opens a LiteAgentClient,
    sends exactly one prompt, closes it. Use LiteAgentClient directly for
    multi-turn conversations so history and any fusion sidekick context
    persist across calls.
    """
    async with (
        LiteAgentClient(options=options, history=history) as agent,
        aclosing(agent.query(prompt)) as events,
    ):
        async for message in events:
            yield message
