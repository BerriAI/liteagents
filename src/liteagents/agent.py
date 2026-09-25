from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

from .fusion import FusionOptions, FusionRuntime
from .history import ConversationHistory
from .loop import run_tool_loop
from .profiles import ProfileOptions
from .routers.base import ModelRouter, StaticRouter
from .tools import Tool
from .types import AgentEvent, Message


@dataclass
class LiteAgentOptions:
    profile: ProfileOptions | None = None
    cwd: str | Path = "."
    session_id: str | None = None
    model: str | None = None
    model_router: ModelRouter | None = None
    tools: list[Tool] = field(default_factory=list)
    system: str | None = None
    max_tokens: int = 4096
    max_turns: int = 20
    tool_choice: dict[str, Any] | None = None
    fusion: FusionOptions | None = None
    stream: bool = False
    # Per-client connection/provider options, never process-global credentials.
    model_kwargs: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.profile is not None:
            if self.model is not None or self.model_router is not None or self.fusion is not None:
                raise ValueError(
                    "A v2 profile cannot be combined with legacy model/router/fusion options"
                )
            if self.system is not None or self.model_kwargs or self.stream or self.tool_choice:
                raise ValueError(
                    "Put model settings, system_prompt and streaming in the v2 profile"
                )
            if self.max_turns != 20 or self.max_tokens != 4096:
                raise ValueError("Put max_turns and model_kwargs.max_tokens in the v2 profile")
            return
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
    """Stateful, multi-turn conversation. Owns the conversation history (and
    the fusion sidekick's separate history, if fusion is enabled) for the
    life of the `async with` block, so repeated .query() calls share cache.
    """

    def __init__(self, *, options: LiteAgentOptions, history: Sequence[Message] = ()) -> None:
        self._options = options
        self._runtime: Any = None
        if options.profile is not None:
            if history:
                raise ValueError("Use session_id for native harness conversation continuation")
            if options.profile.temporal:
                try:
                    from .temporal.client import TemporalRuntime
                except ModuleNotFoundError as exc:
                    from .errors import MissingDependencyError

                    raise MissingDependencyError(
                        "Install liteagents[temporal] for durable runs"
                    ) from exc
                runtime: Any = TemporalRuntime
            else:
                from .runtime.direct import DirectRuntime

                runtime = DirectRuntime
            self._runtime = runtime(
                options.profile,
                cwd=Path(options.cwd).resolve(),
                tools=options.tools,
                session_id=options.session_id,
            )
            return
        self._router: ModelRouter = options.model_router or StaticRouter(options.model)  # type: ignore[arg-type]
        self._history = ConversationHistory(history)
        self._turn = 0
        self._fusion: FusionRuntime | None = None

    async def __aenter__(self) -> Self:
        if self._runtime is not None:
            await self._runtime.open()
            return self
        if self._options.fusion is not None:
            self._fusion = FusionRuntime(
                main_tools=self._options.tools,
                options=self._options.fusion,
                model_kwargs=self._options.model_kwargs,
            )
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._runtime is not None:
            await self._runtime.close()

    async def query(
        self, prompt: str, *, run_id: str | None = None
    ) -> AsyncGenerator[AgentEvent, None]:
        if self._runtime is not None:
            async with aclosing(self._runtime.query(prompt, run_id=run_id)) as events:
                async for event in events:
                    yield event
            return
        if run_id is not None:
            raise ValueError("run_id requires a v2 profile")
        self._turn += 1
        tools = self._fusion.tools_for_main_loop() if self._fusion else self._options.tools
        # run_tool_loop reads history.raw()/history.messages but never mutates
        # them with the incoming prompt itself -- the caller owns that write.
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
            async for message in events:
                yield message

    @property
    def history(self) -> list[Message]:
        if self._runtime is not None:
            return list(self._runtime.history)
        return list(self._history.messages)

    async def get_run(self, run_id: str) -> Any:
        if self._runtime is None:
            raise ValueError("get_run requires a v2 profile")
        return await self._runtime.get_run(run_id)

    async def start_run(self, prompt: str, *, run_id: str | None = None) -> Any:
        """Submit a durable run and return immediately, without subscribing."""
        if self._runtime is None or not hasattr(self._runtime, "start_run"):
            raise ValueError("start_run requires a profile with Temporal enabled")
        return await self._runtime.start_run(prompt, run_id=run_id)


async def query(
    *,
    prompt: str,
    options: LiteAgentOptions,
    history: Sequence[Message] = (),
    run_id: str | None = None,
) -> AsyncGenerator[AgentEvent, None]:
    """Stateless single-prompt convenience wrapper -- opens a LiteAgentClient,
    sends exactly one prompt, closes it. Use LiteAgentClient directly for
    multi-turn conversations so history and any fusion sidekick context
    persist across calls.
    """
    async with (
        LiteAgentClient(options=options, history=history) as agent,
        aclosing(agent.query(prompt, run_id=run_id)) as events,
    ):
        async for message in events:
            yield message
