from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from .fusion import FusionOptions, FusionRuntime
from .history import ConversationHistory
from .loop import run_tool_loop
from .routers.base import ModelRouter, StaticRouter
from .tools import Tool
from .types import Message


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

    def __post_init__(self) -> None:
        if self.model is None and self.model_router is None:
            raise ValueError("LiteAgentOptions requires model or model_router")


class LiteAgentClient:
    """Stateful, multi-turn conversation. Owns the conversation history (and
    the fusion sidekick's separate history, if fusion is enabled) for the
    life of the `async with` block, so repeated .query() calls share cache.
    """

    def __init__(self, *, options: LiteAgentOptions) -> None:
        self._options = options
        self._router: ModelRouter = options.model_router or StaticRouter(options.model)  # type: ignore[arg-type]
        self._history = ConversationHistory()
        self._turn = 0
        self._fusion: FusionRuntime | None = None

    async def __aenter__(self) -> LiteAgentClient:
        if self._options.fusion is not None:
            self._fusion = FusionRuntime(main_tools=self._options.tools, options=self._options.fusion)
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None  # no subprocess or connection to tear down; kept for API symmetry

    async def query(self, prompt: str) -> AsyncIterator[Message]:
        self._turn += 1
        tools = self._fusion.tools_for_main_loop() if self._fusion else self._options.tools
        # run_tool_loop reads history.raw()/history.messages but never mutates
        # them with the incoming prompt itself -- the caller owns that write.
        self._history.add_user_text(prompt)
        async for message in run_tool_loop(
            history=self._history,
            router=self._router,
            tools=tools,
            system=self._options.system,
            max_tokens=self._options.max_tokens,
            max_turns=self._options.max_turns,
            turn_index=self._turn,
            prompt_for_router=prompt,
            tool_choice=self._options.tool_choice,
        ):
            yield message

    @property
    def history(self) -> list[Message]:
        return list(self._history.messages)


async def query(*, prompt: str, options: LiteAgentOptions) -> AsyncIterator[Message]:
    """Stateless single-prompt convenience wrapper -- opens a LiteAgentClient,
    sends exactly one prompt, closes it. Use LiteAgentClient directly for
    multi-turn conversations so history and any fusion sidekick context
    persist across calls.
    """
    async with LiteAgentClient(options=options) as agent:
        async for message in agent.query(prompt):
            yield message
