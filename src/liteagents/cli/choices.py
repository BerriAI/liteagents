"""What `liteagents init` offers: harnesses, providers, and model search."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

import httpx


@dataclass(frozen=True, slots=True)
class Harness:
    label: str
    id: str
    hint: str


@dataclass(frozen=True, slots=True)
class Provider:
    label: str
    key_env: str
    models: tuple[str, ...]
    prefix: str = ""


HARNESSES = (
    Harness("Claude Agent SDK", "claude-sdk", "Claude Code's agent loop"),
    Harness("Codex", "codex", "OpenAI's coding agent loop"),
    Harness("OpenCode", "opencode-v2", "Open source coding agent loop (needs the opencode CLI)"),
    Harness("Pydantic AI", "pydantic-ai", "Typed agents in plain Python"),
    Harness("DeepAgents", "deepagents", "Planning agents built on LangGraph"),
)
GATEWAY = Provider("LiteLLM AI Gateway", "ANTHROPIC_AUTH_TOKEN", (), "litellm_proxy/")
PROVIDERS = (
    Provider(
        "Anthropic", "ANTHROPIC_API_KEY",
        ("anthropic/claude-opus-5-5", "anthropic/claude-sonnet-5-5", "anthropic/claude-haiku-4-5"),
    ),
    Provider("OpenAI", "OPENAI_API_KEY", ("openai/gpt-5.6", "openai/gpt-5.5")),
    GATEWAY,
)
GATEWAY_URL_ENV = "ANTHROPIC_BASE_URL"
DEFAULT_GATEWAY_URL = "http://localhost:4000"
NON_CHAT = re.compile(
    r"image|tts|embed|whisper|transcri|realtime|audio|sora|veo|lyria|rerank|moderation"
    r"|diffusion|stability|imagen|ocr|/\d+-x-\d+/"
)


def harness_by_id(harness_id: str) -> Harness | None:
    return next((h for h in HARNESSES if h.id == harness_id), None)


def provider_for_model(model: str) -> Provider | None:
    """The provider whose credentials a model ID needs, when LiteAgents knows it."""
    if model.startswith(GATEWAY.prefix):
        return GATEWAY
    vendor = model.split("/", 1)[0]
    return next((p for p in PROVIDERS if p.models and p.models[0].startswith(vendor + "/")), None)


def score(query: str, item: str) -> int:
    """0 unless every word matches; prefers words that start the model name, then shorter IDs."""
    text = item.lower()
    words = query.lower().split()
    if not all(word in text for word in words):
        return 0
    tail = text.rsplit("/", 1)[-1]
    bonus = sum(10 for word in words if tail.startswith(word) or f"-{word}" in tail)
    return 1000 + bonus - len(text)


def rank(query: str, items: Sequence[str]) -> list[str]:
    if not query.strip():
        return list(items)
    return sorted((item for item in items if score(query, item)), key=lambda i: -score(query, i))


def chat_models(ids: Sequence[str]) -> tuple[str, ...]:
    return tuple(i for i in ids if not NON_CHAT.search(i.lower()))


def gateway_models(base_url: str, api_key: str) -> tuple[str, ...]:
    response = httpx.get(
        base_url.rstrip("/") + "/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=15,
    )
    response.raise_for_status()
    return chat_models([item["id"] for item in response.json()["data"]])
