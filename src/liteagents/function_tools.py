"""Adapt typed application functions to the existing portable Tool contract."""

from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Callable, Sequence
from typing import Any, get_type_hints

from pydantic import ConfigDict, TypeAdapter, create_model

from .errors import ConfigurationError
from .tools import Tool

ToolInput = Tool | Callable[..., Any]


class FunctionTool(Tool):
    def __init__(self, function: Callable[..., Any]):
        self.function = function
        self.name = getattr(function, "__name__", "")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", self.name):
            raise ConfigurationError("Use a named Python function as a tool")
        if inspect.isgeneratorfunction(function) or inspect.isasyncgenfunction(function):
            raise ConfigurationError(f"Tool {self.name} must return a result, not yield results")
        self.description = inspect.getdoc(function) or f"Call {self.name}."
        try:
            hints = get_type_hints(function, include_extras=True)
            fields: dict[str, Any] = {}
            for name, parameter in inspect.signature(function).parameters.items():
                if name.startswith("_"):
                    raise ConfigurationError(
                        f"Tool {self.name}: parameter names must not start with an underscore"
                    )
                if parameter.kind not in (
                    inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY,
                ):
                    raise ConfigurationError(
                        f"Tool {self.name}: use named parameters instead of positional-only, *args, or **kwargs"
                    )
                if name not in hints:
                    raise ConfigurationError(f"Tool {self.name}: add a type hint for {name}")
                default = ... if parameter.default is inspect.Parameter.empty else parameter.default
                fields[name] = (hints[name], default)
            self.arguments = create_model(
                self.name + "Arguments", __config__=ConfigDict(extra="forbid"), **fields,
            )
            self.input_schema = self.arguments.model_json_schema()
        except ConfigurationError:
            raise
        except Exception as exc:
            raise ConfigurationError(f"Cannot generate input schema for tool {self.name}: {exc}") from exc

    async def execute(self, input: dict[str, Any]) -> str:
        arguments = self.arguments.model_validate(input)
        # Keep nested models, enums, and dataclasses as the annotated Python types.
        values = {name: getattr(arguments, name) for name in type(arguments).model_fields}
        if inspect.iscoroutinefunction(self.function):
            result = await self.function(**values)
        else:
            result = await asyncio.to_thread(self.function, **values)
            if inspect.isawaitable(result):
                result = await result
        return result if isinstance(result, str) else TypeAdapter(Any).dump_json(result).decode()


def adapt_tools(tools: Sequence[ToolInput]) -> list[Tool]:
    adapted = []
    names = set()
    for tool in tools:
        if isinstance(tool, Tool):
            item = tool
        elif callable(tool):
            item = FunctionTool(tool)
        else:
            raise TypeError("tools must contain Tool instances or typed Python functions")
        if item.name in names:
            raise ConfigurationError(f"Duplicate registered tool: {item.name}")
        names.add(item.name)
        adapted.append(item)
    return adapted
