from collections.abc import Callable
from datetime import UTC, datetime


def current_time() -> str:
    """Return the current UTC date and time in ISO 8601 format."""
    return datetime.now(UTC).isoformat()


TOOLS: tuple[Callable[..., object], ...] = (current_time,)
