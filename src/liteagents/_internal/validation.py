"""Small strict validators and owned configuration mappings."""

from collections.abc import Iterator, Mapping
from copy import deepcopy
from math import isfinite
from typing import Any, Generic, TypeVar

T = TypeVar("T")


def integer(value: object, name: str, *, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        constraint = "positive" if minimum == 1 else f"at least {minimum}"
        raise ValueError(f"{name} must be an integer, {constraint} (booleans are not integers)")


def fraction(value: object, name: str) -> None:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
        or not isfinite(value) or not 0 < value < 1):
        raise ValueError(f"{name} must be finite and between 0 and 1 (exclusive)")


def nonempty(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


class OwnedMapping(Mapping[str, T], Generic[T]):
    """Read-only configuration owning its values, including nested containers.

    Reads return detached values, so callers cannot mutate nested configuration.
    Unlike MappingProxyType this remains safe to deepcopy at plugin boundaries.
    """

    def __init__(self, values: Mapping[str, T]) -> None:
        self.__values = deepcopy(dict(values))

    def __getitem__(self, key: str) -> T:
        return deepcopy(self.__values[key])

    def __iter__(self) -> Iterator[str]:
        return iter(self.__values)

    def __len__(self) -> int:
        return len(self.__values)

    def __deepcopy__(self, memo: dict[int, Any]) -> "OwnedMapping[T]":
        return self
