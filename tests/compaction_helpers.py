"""Small data builders shared by the compaction tests; no scenario runner."""

from liteagents import (
    AssistantMessage,
    CompactionCompleted,
    CompactionOptions,
    RecentTokens,
    Summarize,
    TextBlock,
    TokenEstimate,
    TokenThreshold,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    apply_compaction,
)


def count_chars(model, text):
    return TokenEstimate(len(text), "test_chars")


def policy(**kwargs):
    defaults = {
        "strategy": Summarize(model="summary", keep=RecentTokens(1), max_tokens=100),
        "trigger": TokenThreshold(tokens=1000),
        "token_counter": count_chars,
        "context_windows": {"main": 100_000, "summary": 100_000, "sidekick": 100_000},
        "safety_margin": 0,
    }
    return CompactionOptions(**(defaults | kwargs))


def old_history():
    return [UserMessage("initial goal"), AssistantMessage([TextBlock("old detail " * 500)], "main")]


def pair(call_id, output):
    return [AssistantMessage([ToolUseBlock(call_id, "echo", {"text": "hi"})], "main"),
            UserMessage([ToolResultBlock(call_id, output)])]


async def run(runtime, history, **kwargs):
    return [event async for event in runtime.run(
        history=history, model="main", system="instructions", tools=[], max_tokens=100,
        **kwargs,
    )]


def replay_events(messages, events):
    """Update a caller's context mirror from public events."""
    messages = list(messages)
    for event in events:
        if isinstance(event, CompactionCompleted):
            messages = apply_compaction(messages, event.update)
        elif isinstance(event, (UserMessage, AssistantMessage)):
            messages.append(event)
    return messages
