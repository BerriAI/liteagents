"""Router protocol and the default static implementation.

A router picks which model handles the next turn, given the running
TurnContext. Anything satisfying `route(self, context) -> str` qualifies,
whether or not it subclasses ModelRouter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..types import TurnContext


@runtime_checkable
class ModelRouter(Protocol):
    """Structural contract for anything that can route a turn to a model."""

    async def route(self, context: TurnContext) -> str: ...


@dataclass
class StaticRouter:
    """Default router: always returns the same model regardless of context.

    Used internally when LiteAgentOptions.model is set and no model_router
    is given.
    """

    model: str

    async def route(self, context: TurnContext) -> str:
        return self.model
