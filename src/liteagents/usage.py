"""Normalized token usage with provider-specific details kept separately."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ._internal.validation import OwnedMapping, integer

_COUNTS = ("input_tokens", "output_tokens", "total_tokens",
           "cache_read_input_tokens", "cache_creation_input_tokens")


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    details: Mapping[str, Any] = field(default_factory=dict)
    stages: tuple["TokenUsage", ...] = ()

    def __post_init__(self) -> None:
        for name in _COUNTS:
            value = getattr(self, name)
            if value is not None:
                integer(value, name)
        object.__setattr__(self, "details", OwnedMapping(self.details))
        object.__setattr__(self, "stages", tuple(self.stages))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "TokenUsage":
        """Normalize untrusted provider counts; invalid values become unavailable."""
        counts = {name: value if type(value := raw.get(name)) is int and value >= 0 else None
                  for name in _COUNTS}
        # Invalid cache counts make the entire input baseline unreliable.
        if any(raw.get(name) is not None and counts[name] is None for name in
               ("cache_read_input_tokens", "cache_creation_input_tokens")):
            counts["input_tokens"] = None
        return cls(input_tokens=counts["input_tokens"], output_tokens=counts["output_tokens"],
                   total_tokens=counts["total_tokens"], cache_read_input_tokens=counts["cache_read_input_tokens"],
                   cache_creation_input_tokens=counts["cache_creation_input_tokens"],
                   details={key: value for key, value in raw.items() if key not in _COUNTS})

    @property
    def context_input_tokens(self) -> int | None:
        if self.input_tokens is None:
            return None
        total = (self.input_tokens + (self.cache_read_input_tokens or 0)
                 + (self.cache_creation_input_tokens or 0))
        return total or None

    def to_dict(self) -> dict[str, Any]:
        result = dict(self.details)
        result.update({name: value for name in _COUNTS if (value := getattr(self, name)) is not None})
        if self.stages:
            result["stages"] = [stage.to_dict() for stage in self.stages]
        return result

    @classmethod
    def combine(cls, stages: Sequence["TokenUsage"]) -> "TokenUsage | None":
        if not stages:
            return None
        totals = {}
        for name in _COUNTS:
            values = [value for stage in stages if (value := getattr(stage, name)) is not None]
            totals[name] = sum(values) if values else None
        return cls(input_tokens=totals["input_tokens"], output_tokens=totals["output_tokens"],
                   total_tokens=totals["total_tokens"], cache_read_input_tokens=totals["cache_read_input_tokens"],
                   cache_creation_input_tokens=totals["cache_creation_input_tokens"], stages=tuple(stages))
