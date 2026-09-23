"""The core tool-calling loop: call the model, run any requested tools, repeat."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import aclosing
from copy import deepcopy
from typing import Any

import litellm

from ._internal.adapter import extract_response_fields, tool_result_block
from ._internal.compaction_runtime import CompactionRuntime
from ._internal.streaming import stream_response
from .compaction.tokens import TokenCountRequest
from .history import ConversationHistory
from .routers.base import ModelRouter
from .tools import Tool, find_tool
from .types import AgentEvent, TextDelta, ToolUseBlock, TurnContext


async def run_tool_loop(
    *,
    history: ConversationHistory,
    router: ModelRouter,
    tools: list[Tool],
    system: str | None,
    max_tokens: int,
    max_turns: int,
    turn_index: int,
    prompt_for_router: str,
    tool_choice: dict[str, Any] | None = None,
    stream: bool = False,
    model_kwargs: dict[str, Any] | None = None,
    compaction: CompactionRuntime | None = None,
) -> AsyncGenerator[AgentEvent, None]:
    """Runs model-call -> tool-execution rounds for ONE user turn, until the
    model stops requesting tools or max_turns is hit. Mutates `history` in
    place and yields each message (assistant responses, and the synthetic
    user message carrying tool results) as it happens.

    The router is called once per ROUND (not once per user turn) -- if a
    turn takes two model calls (tool_use, then a final answer), route() is
    asked again for the second call. Simple routers are pure functions of
    context.prompt so this is invisible; a memoizing router can key on
    context.turn to make repeat rounds within one turn cheap and stable.
    """
    if len({tool.name for tool in tools}) != len(tools):
        raise ValueError("Tool names must be unique; use prefixes for MCP tools from different servers")
    anthropic_tools = [t.to_anthropic_tool() for t in tools] or None

    for _round in range(max_turns):
        # Snapshot both lists -- history keeps mutating after this point, and
        # a router or caller holding onto `context`/the call kwargs should
        # see the state as of this round, not whatever history grows into.
        context = TurnContext(prompt=prompt_for_router, history=deepcopy(history.messages), turn=turn_index)
        model = await router.route(context)

        if compaction is not None:
            async with aclosing(compaction.run(
                history=history, model=model, system=system, tools=tools, max_tokens=max_tokens,
                model_kwargs=model_kwargs, tool_choice=tool_choice,
            )) as compaction_events:
                async for compaction_event in compaction_events:
                    yield compaction_event

        request_kwargs = dict(model_kwargs or {})
        if stream:
            request_kwargs["stream"] = True
        counted_request = (TokenCountRequest(tuple(deepcopy(history.raw())), system,
                                             tuple(deepcopy(anthropic_tools or [])))
                           if compaction is not None else None)
        response = await litellm.anthropic_messages(
            model=model,
            messages=list(history.raw()),
            system=system,
            max_tokens=max_tokens,
            tools=anthropic_tools,
            tool_choice=tool_choice,
            **request_kwargs,
        )

        if stream:
            async with aclosing(stream_response(response, model=model)) as events:
                async for event in events:
                    if isinstance(event, TextDelta):
                        yield event
                    else:
                        response = event

        content_dicts, stop_reason, response_model, usage = extract_response_fields(response)
        if compaction is not None and counted_request is not None:
            compaction.context_tokens.observe(
                model, counted_request, {"tool_choice": tool_choice, **(model_kwargs or {})},
                usage, stop_reason,
            )
        assistant_message = history.add_assistant_response(content_dicts, model=response_model or model)
        assistant_message.stop_reason = stop_reason
        assistant_message.usage = usage.to_dict() if usage is not None else None
        yield deepcopy(assistant_message)

        if stop_reason != "tool_use":
            return

        tool_use_blocks = [b for b in assistant_message.content if isinstance(b, ToolUseBlock)]
        if not tool_use_blocks:
            return

        # Sequential, not gathered concurrently -- a deliberate simplicity
        # choice (easier to reason about and log), not an oversight.
        results: list[dict[str, Any]] = []
        for block in tool_use_blocks:
            tool = find_tool(tools, block.name)
            if tool is None:
                results.append(tool_result_block(block.id, f"Error: no tool named {block.name!r}", is_error=True))
                continue
            try:
                output = await tool.execute(block.input)
                results.append(tool_result_block(block.id, output))
            except Exception as exc:  # noqa: BLE001 -- a broken tool becomes an error result, not a crash
                results.append(tool_result_block(block.id, f"Error: {exc}", is_error=True))

        yield deepcopy(history.add_user_tool_results(results))

    # max_turns exhausted while the model still wants tools: stop here. The
    # caller already saw the last AssistantMessage (stop_reason "tool_use")
    # via the yield above and can inspect that if it needs to detect this.
