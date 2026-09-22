"""Router backed by JEV, a third-party turn-classification service
(docs.typesafe.ai). Not part of litellm and not vendored anywhere in this
codebase -- reached purely over HTTP.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

import httpx

from ..types import TurnContext


class JevClassificationError(Exception):
    """Raised when the JEV classification request fails or returns something unusable."""


@dataclass(frozen=True)
class JevTier:
    name: str
    model: str
    description: str


async def _classify(
    *,
    prompt: str,
    tiers: Sequence[JevTier],
    api_key: str,
    base_url: str,
    timeout: float,
) -> str:
    """Call JEV's /classify endpoint and return the chosen tier name.

    NOTE: this HTTP contract is illustrative/best-effort -- JEV's actual API
    is an external service not specified anywhere in this codebase. Kept
    isolated in this one function so the contract can be corrected later
    without touching JevModelRouter's routing/fallback logic.
    """
    payload = {
        "prompt": prompt,
        "tiers": [{"name": t.name, "description": t.description} for t in tiers],
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/classify",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as exc:
        raise JevClassificationError(str(exc)) from exc
    except ValueError as exc:
        raise JevClassificationError(f"invalid JSON response: {exc}") from exc

    tier_name = data.get("tier")
    if not isinstance(tier_name, str):
        raise JevClassificationError(f"response missing 'tier' string: {data!r}")
    return tier_name


class JevModelRouter:
    """Classifies each turn against a set of JevTiers and routes to the
    cheapest tier predicted to handle it, per docs.typesafe.ai.

    Falls back to `fallback_model` on any classification error (missing
    api_key, bad network, timeout, malformed response, unknown tier name)
    so a JEV outage degrades to "always use the safe model" rather than
    raising into the caller's loop.

    Memoizes the resolved model by TurnContext.turn (not per-round), so a
    tool-use round-trip mid-turn doesn't reclassify and silently switch
    models partway through a single user turn.
    """

    def __init__(
        self,
        *,
        tiers: Sequence[JevTier],
        fallback_model: str,
        api_key: str | None = None,
        base_url: str = "https://api.typesafe.ai/v1",
        timeout: float = 5.0,
    ) -> None:
        self._tiers = tuple(tiers)
        self._by_name = {t.name: t for t in self._tiers}
        self._fallback_model = fallback_model
        self._api_key = api_key or os.environ.get("TYPESAFE_API_KEY")
        self._base_url = base_url
        self._timeout = timeout
        self._turn_cache: dict[int, str] = {}

    async def route(self, context: TurnContext) -> str:
        if context.turn in self._turn_cache:
            return self._turn_cache[context.turn]

        model = self._fallback_model
        if self._api_key:
            try:
                tier_name = await _classify(
                    prompt=context.prompt,
                    tiers=self._tiers,
                    api_key=self._api_key,
                    base_url=self._base_url,
                    timeout=self._timeout,
                )
                tier = self._by_name.get(tier_name)
                if tier is not None:
                    model = tier.model
            except JevClassificationError:
                pass

        self._turn_cache[context.turn] = model
        return model
