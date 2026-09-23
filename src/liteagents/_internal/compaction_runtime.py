"""Per-conversation compaction lifecycle and atomic application of strategy results."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from copy import deepcopy
from typing import Any

from ..compaction import (
    CompactionError,
    CompactionOptions,
    ContextBudgetExceeded,
    TriggerContext,
    context_window,
)
from ..compaction.background import BackgroundMemoryOptions
from ..history import ConversationHistory
from ..tools import Tool
from ..types import (
    CompactionCompleted,
    CompactionEvent,
    CompactionFailed,
    CompactionReason,
    CompactionSkipped,
    CompactionStarted,
)
from ..usage import TokenUsage
from .compaction_context import HistorySnapshot, RuntimeContext, SummaryService, TokenMeasurements
from .context_tokens import ContextTokens
from .memory_runtime import BackgroundMemoryRuntime


class CompactionRuntime:
    def __init__(self, options: CompactionOptions) -> None:
        self.options = options
        self.state: Any = None
        self.context_tokens = ContextTokens()
        self.last_model: str | None = None

    async def cancel_pending(self) -> None:
        """Synchronous strategies have no background work to cancel."""

    async def run(
        self, *, history: ConversationHistory, model: str, system: str | None,
        tools: list[Tool], max_tokens: int, model_kwargs: dict[str, Any] | None = None,
        manual: bool = False, instructions: str | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> AsyncGenerator[CompactionEvent, None]:
        self.last_model = model
        options = self.options
        measurements = TokenMeasurements(options)
        snapshot = HistorySnapshot(history.snapshot(), model, system,
            tuple(tool.to_anthropic_tool() for tool in tools), measurements)
        settings = {"tool_choice": tool_choice, **(model_kwargs or {})}
        reason: CompactionReason = "manual" if manual else "threshold"
        usage: TokenUsage | None = None
        try:
            before = self.context_tokens.count(model, snapshot.request, settings, measurements.count)
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

            if not manual and not over_budget:
                assert options.trigger is not None
                trigger_context = TriggerContext(tuple(deepcopy(snapshot.history.messages)),
                                                 model, before, budget, reason)
                if not options.trigger.should_compact(trigger_context):
                    return
            # Validate complete tool groups before handing history to a strategy.
            _ = snapshot.boundaries
            context = RuntimeContext(snapshot, SummaryService(options, measurements, model_kwargs or {}),
                                     before, budget, reason, target, instructions, deepcopy(self.state))
            yield CompactionStarted(reason, model, before)
            result = await options.strategy.compact(context)
            if result is None:
                if over_budget:
                    raise ContextBudgetExceeded("No safe reduction is available for this input budget")
                yield CompactionSkipped(reason, model, "No applicable reduction")
                return
            usage = deepcopy(result.usage)
            candidate = snapshot.apply(result.update)
            after = candidate.local_tokens
            if after.tokens >= snapshot.local_tokens.tokens:
                if over_budget:
                    raise ContextBudgetExceeded("Compaction did not reduce the oversized context")
                yield CompactionSkipped(reason, model, "Proposed context was not smaller", usage)
                return
            if budget is not None and after.tokens > budget:
                raise ContextBudgetExceeded("Compacted context still exceeds the selected model's input budget")
            # Everything that can fail is prepared before either history or state is committed.
            state = deepcopy(result.state)
            event = CompactionCompleted(reason, model, before, after,
                                        deepcopy(result.update), deepcopy(result.usage))
            history.commit(candidate.history, expected_version=snapshot.history.version)
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


def make_compaction_runtime(
    options: CompactionOptions | BackgroundMemoryOptions | None,
) -> CompactionRuntime | BackgroundMemoryRuntime | None:
    if isinstance(options, BackgroundMemoryOptions):
        return BackgroundMemoryRuntime(options)
    return CompactionRuntime(options) if options is not None else None
