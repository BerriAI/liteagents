import os
from collections.abc import AsyncIterator
from pathlib import Path

from liteagents import (
    AgentEvent,
    AssistantMessage,
    FeatureOptions,
    LiteAgentClient,
    ProfileOptions,
    TextBlock,
)
from liteagents.project import AgentConfig

from .memory import sessions
from .tools import TOOLS

ROOT = Path(__file__).resolve().parent.parent
INSTRUCTIONS = ROOT / "agent" / "instructions.md"


def load_config() -> AgentConfig:
    return AgentConfig.load(ROOT / "agent.toml")


def model_kwargs(model: str) -> dict[str, str]:
    if not model.startswith("litellm_proxy/"):
        return {}
    gateway = {
        "api_base": os.environ.get("ANTHROPIC_BASE_URL", ""),
        "api_key": os.environ.get("ANTHROPIC_AUTH_TOKEN", ""),
    }
    return {key: value for key, value in gateway.items() if value}


def build_profile(config: AgentConfig, *, streaming: bool = False) -> ProfileOptions:
    return ProfileOptions(
        harness=config.harness,
        model=config.model,
        model_kwargs=model_kwargs(config.model),
        system_prompt=INSTRUCTIONS.read_text(),
        tools=[tool.__name__ for tool in TOOLS],
        features=FeatureOptions(streaming=streaming),
        harness_options={"claude-sdk": {"env": {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}}},
    )


async def stream_turn(
    text: str, conversation_id: str, *, streaming: bool = False
) -> AsyncIterator[AgentEvent]:
    config = load_config()
    async with LiteAgentClient(
        profile=build_profile(config, streaming=streaming),
        cwd=ROOT,
        tools=list(TOOLS),
        **sessions.client_options(conversation_id, config.harness),
    ) as client:
        async for event in client.query(text):
            yield event
        sessions.save(conversation_id, config.harness, client)


async def run_turn(text: str, conversation_id: str) -> str:
    final = ""
    async for event in stream_turn(text, conversation_id):
        if isinstance(event, AssistantMessage) and event.stop_reason != "tool_use":
            final = "".join(b.text for b in event.content if isinstance(b, TextBlock))
    return final
