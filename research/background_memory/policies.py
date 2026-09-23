"""Research-only alternatives; these are not extra SDK configuration commitments."""

from __future__ import annotations

import json
from dataclasses import replace

from liteagents._internal.memory_observer import Observation, combined_usage, observe
from liteagents.compaction.tokens import TokenCountRequest

JSON_STATE = """Represent working memory as one compact JSON object with keys goal,
constraints, current_state, decisions, open_work, evidence_index. Use maps for current facts,
not chronological lists. An evidence entry is only a short retrieval key and source message IDs.
No markdown. Do not include empty fields or repeat the same fact in different sections."""

TERSE_STATE = """Use a compact task-state record. Prefer key=value facts over prose.
Keep only current constraints, decisions, verified state, unfinished work, and an evidence index.
Use source references for resolved detail. Do not repeat the objective or acknowledged updates."""


async def delta_observer(request, options, model_kwargs):
    """Append small immutable updates; rebuild a checkpoint only when the log fills.

    The original source events feed both delta and checkpoint calls. All paid
    attempts contribute usage, and the normal runtime still owns cursor commits.
    """
    delta_request = replace(request, system=request.system + "\nFor this call return ONLY a terse "
                            "incremental update to previous_notes: changed facts, corrections, new decisions "
                            "or open work, with source IDs. Do not repeat unchanged facts. If no task-relevant "
                            "state changed, return exactly NO_CHANGE. A correction must identify what it supersedes.")
    delta_options = replace(options, max_memory_tokens=min(240, options.max_memory_tokens))
    delta = await observe(delta_request, delta_options, model_kwargs)
    previous = json.loads(request.text)["previous_notes"]
    if delta.memory.notes.strip().strip("`") == "NO_CHANGE":
        notes = previous or "No task-relevant state recorded. Recover original details from history."
    else:
        notes = (previous + "\n\nUpdate (later facts supersede earlier ones):\n" + delta.memory.notes).strip()
    if options.token_counter(options.model, TokenCountRequest(({"role": "user", "content": notes},))).tokens <= options.max_memory_tokens:
        return Observation(replace(delta.memory, notes=notes), delta.usage)
    checkpoint = await observe(request, options, model_kwargs)
    usage = combined_usage([u for u in (delta.usage, checkpoint.usage) if u is not None])
    return Observation(checkpoint.memory, usage)


def eager_input_scheduler(original_start):
    """Let observation include the incoming human request while the main model works.

    Publication still pins that request, and all normal coverage and hard-limit
    checks remain in the SDK. Only when observation starts changes here.
    """
    from liteagents import UserMessage

    def start(runtime, history, **kwargs):
        if (history.messages and isinstance(history.messages[-1], UserMessage)
                and isinstance(history.messages[-1].content, str)):
            kwargs["complete_turn"] = True
        return original_start(runtime, history, **kwargs)

    return start
