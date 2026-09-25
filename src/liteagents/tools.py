"""The Tool interface.

Deliberately minimal: a tool is a name, a description, a JSON schema for
its input, and one async method. No registry class -- callers just pass
list[Tool] around, and find_tool() below is the only lookup helper needed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .types import WireTool


class Tool(ABC):
    """Base class for a tool the model can call.

    Subclasses set `name`, `description`, and `input_schema` as class (or
    instance) attributes, and implement `execute`. `to_anthropic_tool()`
    converts to the wire shape litellm.anthropic_messages() expects in
    `tools=`.
    """

    name: str
    description: str
    input_schema: dict[str, Any]

    @abstractmethod
    async def execute(self, input: dict[str, Any]) -> str | list[dict[str, Any]]:
        """Run the tool and return its result as a string.

        Raise on failure -- the tool loop catches exceptions and turns them
        into an `is_error` tool_result, so tools don't need their own
        try/except for this.
        """

    def to_anthropic_tool(self) -> WireTool:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


def find_tool(tools: list[Tool], name: str) -> Tool | None:
    for tool in tools:
        if tool.name == name:
            return tool
    return None
