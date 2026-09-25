"""Public background-memory options share the regular client and fusion APIs."""

from liteagents import (
    BackgroundMemoryOptions,
    FusionOptions,
    LiteAgentClient,
    LiteAgentOptions,
    MemorySnapshot,
    Message,
)


async def example() -> None:
    memory = BackgroundMemoryOptions(model="memory", max_context_tokens=8000)
    options = LiteAgentOptions(model="main", compaction=memory,
                               fusion=FusionOptions(sidekick_model="sidekick", sidekick_compaction=memory))
    async with LiteAgentClient(options=options) as client:
        async for _ in client.query("Continue the task"):
            pass
        snapshot: MemorySnapshot | None = client.memory
        transcript: list[Message] = client.transcript
        assert snapshot is not None and transcript
