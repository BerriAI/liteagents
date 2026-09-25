"""Opt-in v1 compatibility client. New applications should use profile-driven v2."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any, Self

from ..tools import Tool
from ..types import AgentEvent, Message
from .fusion import FusionOptions, FusionRuntime
from .history import ConversationHistory
from .loop import run_tool_loop
from .routers.base import ModelRouter, StaticRouter


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
    stream: bool = False
    model_kwargs: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self):
        if self.model is None and self.model_router is None:
            raise ValueError("LiteAgentOptions requires model or model_router")
        reserved = {"model", "messages", "system", "max_tokens", "tools", "tool_choice", "stream"}
        if reserved.intersection(self.model_kwargs):
            raise ValueError(
                "Use LiteAgentOptions fields for model, messages, tools and stream settings"
            )
        if self.max_turns < 1:
            raise ValueError("max_turns must be at least 1")


class LiteAgentClient:
    def __init__(self, *, options: LiteAgentOptions, history: Sequence[Message] = ()):
        self._options = options
        self._router = options.model_router or StaticRouter(options.model or "")
        self._history = ConversationHistory(history)
        self._turn = 0
        self._fusion: FusionRuntime | None = None

    async def __aenter__(self) -> Self:
        if self._options.fusion is not None:
            self._fusion = FusionRuntime(
                main_tools=self._options.tools,
                options=self._options.fusion,
                model_kwargs=self._options.model_kwargs,
            )
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        pass

    async def query(self, prompt: str) -> AsyncGenerator[AgentEvent, None]:
        self._turn += 1
        tools = self._fusion.tools_for_main_loop() if self._fusion else self._options.tools
        self._history.add_user_text(prompt)
        async with aclosing(
            run_tool_loop(
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
            )
        ) as events:
            async for event in events:
                yield event

    @property
    def history(self) -> list[Message]:
        return list(self._history.messages)


async def query(
    *, prompt: str, options: LiteAgentOptions, history: Sequence[Message] = ()
) -> AsyncGenerator[AgentEvent, None]:
    async with (
        LiteAgentClient(options=options, history=history) as client,
        aclosing(client.query(prompt)) as events,
    ):
        async for event in events:
            yield event
