"""Start compaction when every child trigger matches."""

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass

from ..base import CompactionTrigger, TriggerContext


@dataclass(frozen=True)
class AllOf:
    triggers: tuple[CompactionTrigger, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "triggers", tuple(self.triggers))
        if not self.triggers:
            raise ValueError("all_of requires at least one trigger")

    def should_compact(self, context: TriggerContext) -> bool:
        return all(trigger.should_compact(deepcopy(context)) for trigger in self.triggers)


def all_of(triggers: Sequence[CompactionTrigger]) -> AllOf:
    """Construct a trigger; evaluate children in order, stopping on the first miss."""
    return AllOf(tuple(triggers))
