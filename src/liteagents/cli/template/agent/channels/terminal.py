from liteagents.project import chat

from ..core import load_config, stream_turn


async def main() -> None:
    await chat(load_config().name, lambda text, conversation_id: stream_turn(
        text, conversation_id, streaming=True
    ))
