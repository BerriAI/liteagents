"""Per-conversation compaction lifecycle and atomic application of strategy results."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from copy import deepcopy
from dataclasses import replace
from typing import Any

from ..compaction import (
    CompactionContext,
    CompactionError,
    CompactionOptions,
    ContextBudgetExceeded,
    TokenCountRequest,
    TokenEstimate,
    context_window,
)
from ..history import ConversationHistory, safe_boundaries
from ..tools import Tool
from ..types import (
    CompactionCompleted,
    CompactionEvent,
    CompactionFailed,
    CompactionReason,
    CompactionSkipped,
    CompactionStarted,
)
from .context_tokens import ContextTokens


class CompactionRuntime:
    def __init__(self, options: CompactionOptions) -> None:
        self.options = options
        self.state: Any = None
        self.context_tokens = ContextTokens()
        self.last_model: str | None = None

    async def run(
        self, *, history: ConversationHistory, model: str, system: str | None,
        tools: list[Tool], max_tokens: int, model_kwargs: dict[str, Any] | None = None,
        manual: bool = False, instructions: str | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> AsyncGenerator[CompactionEvent, None]:
        self.last_model = model
        options = self.options
        snapshot = history.snapshot()
        schemas = [tool.to_anthropic_tool() for tool in tools] or None

        settings = {"tool_choice": tool_choice, **(model_kwargs or {})}

        def request(candidate: ConversationHistory) -> TokenCountRequest:
            return TokenCountRequest(tuple(deepcopy(candidate.raw())), system,
                                     tuple(deepcopy(schemas or [])))

        def count(candidate: ConversationHistory) -> TokenEstimate:
            return options.token_counter(model, request(candidate))

        reason: CompactionReason = "manual" if manual else "threshold"
        usage: dict[str, Any] | None = None
        try:
            before = self.context_tokens.count(model, request(snapshot), settings, options.token_counter)
            window = context_window(model, options.context_windows)
            budget = window - max_tokens - options.safety_margin if window is not None else None
            if budget is not None and budget <= 0:
                raise ContextBudgetExceeded("Output reservation leaves no usable input budget")
            over_budget = budget is not None and before.tokens > budget
            if not manual and options.trigger is None:
                if over_budget:
                    raise ContextBudgetExceeded("Context exceeds the input budget; call compact() manually")
                return
            if not manual and over_budget:
                reason = "budget"
            target = options.target_tokens
            if target is not None and budget is not None:
                target = min(target, budget)

            def make_context(candidate: ConversationHistory) -> CompactionContext:
                return CompactionContext(
                    messages=tuple(deepcopy(candidate.messages)), model=model,
                    tokens=before if candidate is snapshot else count(candidate), input_budget=budget,
                    message_tokens=tuple(options.token_counter(
                        model, TokenCountRequest((deepcopy(message),))
                    ).tokens for message in candidate.raw()),
                    boundaries=safe_boundaries(candidate.messages), reason=reason,
                    system=system, instructions=instructions, state=deepcopy(self.state),
                    model_kwargs=deepcopy(model_kwargs or {}),
                    context_windows=dict(options.context_windows), token_counter=options.token_counter,
                    safety_margin=options.safety_margin, target_tokens=target,
                    _preview=lambda update: make_context(candidate.compacted(update)),
                )

            context = make_context(snapshot)
            if not manual and not over_budget:
                assert options.trigger is not None
                # Triggers receive their own detached state/history, just like strategies.
                if not options.trigger.should_compact(replace(
                    context, messages=tuple(deepcopy(context.messages)), state=deepcopy(context.state),
                    model_kwargs=deepcopy(context.model_kwargs),
                    context_windows=dict(context.context_windows),
                )):
                    return
            yield CompactionStarted(reason, model, before.tokens, before.source)
            result = await options.strategy.compact(context)
            if result is None:
                if over_budget:
                    raise ContextBudgetExceeded("No safe reduction is available for this input budget")
                yield CompactionSkipped(reason, model, "No applicable reduction")
                return
            usage = deepcopy(result.usage)
            candidate = snapshot.compacted(result.update)
            after = count(candidate)
            if after.tokens >= count(snapshot).tokens:
                if over_budget:
                    raise ContextBudgetExceeded("Compaction did not reduce the oversized context")
                yield CompactionSkipped(reason, model, "Proposed context was not smaller", usage)
                return
            if budget is not None and after.tokens > budget:
                raise ContextBudgetExceeded("Compacted context still exceeds the selected model's input budget")
            # Everything that can fail is prepared before either history or state is committed.
            state = deepcopy(result.state)
            event = CompactionCompleted(reason, model, before.tokens, after.tokens, before.source,
                                        deepcopy(result.update), deepcopy(result.usage),
                                        token_source_after=after.source)
            history.commit(candidate, expected_version=snapshot.version)
            self.context_tokens.clear()
            self.state = state
            yield event
        except Exception as exc:
            if isinstance(exc, CompactionError) and exc.usage is not None:
                usage = exc.usage
            yield CompactionFailed(reason, model, str(exc), usage)
            if isinstance(exc, CompactionError):
                exc.usage = usage
                raise
            raise CompactionError(str(exc), usage=usage) from exc
