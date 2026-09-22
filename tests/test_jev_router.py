from __future__ import annotations

import httpx
import pytest

from liteagents import JevModelRouter, JevTier, TurnContext
from liteagents.routers.jev import JevClassificationError, _classify


async def test_falls_back_when_no_api_key():
    router = JevModelRouter(
        tiers=(JevTier(name="FAST", model="openai/gpt-5.4-mini", description="fast"),),
        fallback_model="anthropic/claude-opus-4-8",
        api_key=None,
    )
    model = await router.route(TurnContext(prompt="hi", history=[], turn=1))
    assert model == "anthropic/claude-opus-4-8"


async def test_falls_back_on_classification_error(monkeypatch: pytest.MonkeyPatch):
    async def raise_error(**kwargs):
        raise JevClassificationError("boom")

    monkeypatch.setattr("liteagents.routers.jev._classify", raise_error)
    router = JevModelRouter(
        tiers=(JevTier(name="FAST", model="openai/gpt-5.4-mini", description="fast"),),
        fallback_model="anthropic/claude-opus-4-8",
        api_key="fake-key",
    )
    model = await router.route(TurnContext(prompt="hi", history=[], turn=1))
    assert model == "anthropic/claude-opus-4-8"


async def test_memoizes_by_turn(monkeypatch: pytest.MonkeyPatch):
    calls = []

    async def record_and_return(**kwargs):
        calls.append(kwargs["prompt"])
        return "FAST"

    monkeypatch.setattr("liteagents.routers.jev._classify", record_and_return)
    router = JevModelRouter(
        tiers=(JevTier(name="FAST", model="openai/gpt-5.4-mini", description="fast"),),
        fallback_model="anthropic/claude-opus-4-8",
        api_key="fake-key",
    )
    context = TurnContext(prompt="hi", history=[], turn=1)
    model1 = await router.route(context)
    model2 = await router.route(context)
    assert model1 == model2 == "openai/gpt-5.4-mini"
    assert len(calls) == 1


async def test_classify_raises_on_http_error(monkeypatch: pytest.MonkeyPatch):
    def broken_transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "server error"})

    transport = httpx.MockTransport(broken_transport)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(transport=transport, **{k: v for k, v in kwargs.items() if k != "transport"}),
    )

    with pytest.raises(JevClassificationError):
        await _classify(
            prompt="hi", tiers=(), api_key="k", base_url="https://api.typesafe.ai/v1", timeout=1.0
        )
