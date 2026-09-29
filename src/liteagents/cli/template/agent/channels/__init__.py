from collections.abc import Awaitable, Callable
from importlib import import_module

from liteagents.project import CHANNELS


def load_channel(name: str) -> Callable[[], Awaitable[None]]:
    if name not in CHANNELS:
        raise ValueError(f"Unknown channel {name!r}; choose one of {', '.join(CHANNELS)}")
    return import_module(f"{__name__}.{name}").main
