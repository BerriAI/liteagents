"""Start compaction when any child trigger matches."""

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass

from ..base import CompactionContext, CompactionTrigger


@dataclass(frozen=True)
class AnyOf:
    triggers: tuple[CompactionTrigger, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "triggers", tuple(self.triggers))
        if not self.triggers:
            raise ValueError("any_of requires at least one trigger")

    def should_compact(self, context: CompactionContext) -> bool:
        return any(trigger.should_compact(deepcopy(context)) for trigger in self.triggers)


def any_of(triggers: Sequence[CompactionTrigger]) -> AnyOf:
    """Construct a trigger; evaluate children in order, stopping on the first match."""
    return AnyOf(tuple(triggers))
