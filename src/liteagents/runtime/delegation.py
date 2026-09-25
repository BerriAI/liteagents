"""Named agent tools: each child delegates its loop to the selected native harness."""

from __future__ import annotations

from contextlib import aclosing
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..errors import ConfigurationError
from ..profiles import FeatureOptions, ProfileOptions
from ..runs import RunResult
from ..tools import Tool
from ..types import AssistantMessage, UserMessage
from .control import CURRENT, operation_id
from .events import payload


class DelegateTool(Tool):
    def __init__(self, name: str, parent: ProfileOptions, cwd: Path, tools: list[Tool]):
        self.agent_name, self.parent, self.cwd, self.tools = name, parent, cwd, tools
        options = parent.subagents[name]
        self.name = "delegate_" + name
        self.description = options.description
        self.input_schema = {
            "type": "object",
            "properties": {"prompt": {"type": "string"}},
            "required": ["prompt"],
            "additionalProperties": False,
        }

    async def execute(self, input: dict[str, Any]) -> str:
        from ..harnesses.base import create_adapter

        child = self.parent.subagents[self.agent_name]
        native = dict(self.parent.harness_options)
        instances = native.pop("subagent_model_instances", {})
        native.pop("model_instance", None)
        native.pop("fallback_model_instances", None)
        if self.agent_name in instances:
            native["model_instance"] = instances[self.agent_name]
        options = self.parent.model_copy(
            update={
                "model": child.model or self.parent.model,
                "model_kwargs": {**self.parent.model_kwargs, **child.model_kwargs},
                "system_prompt": child.system_prompt or child.description,
                "tools": child.tools,
                "subagents": {},
                "harness_options": native,
                "features": FeatureOptions(streaming=self.parent.features.streaming),
            }
        )
        identifier = operation_id() or uuid4().hex
        adapter = create_adapter(
            options,
            cwd=self.cwd,
            tools=self.tools,
            session_id=identifier,
            tool_allowlist=set(child.tools),
        )
        control = CURRENT.get()
        if control:
            await control.emit(
                "subagent_started", agent=self.agent_name, child_id=identifier, model=options.model
            )
        messages = []
        try:
            await adapter.open()
            async with aclosing(
                adapter.query(input["prompt"], run_id=identifier, resume=bool(options.temporal))
            ) as events:
                async for event in events:
                    if isinstance(event, (AssistantMessage, UserMessage)):
                        messages.append(event)
                    if control:
                        await control.emit(
                            "subagent_event",
                            agent=self.agent_name,
                            child_id=identifier,
                            event=payload(event),
                        )
            result = RunResult(identifier, options.harness, messages, adapter.native_session_id)
            if control:
                await control.emit("subagent_completed", agent=self.agent_name, child_id=identifier)
            return result.text
        finally:
            await adapter.close()


def delegate_tools(profile: ProfileOptions, cwd: Path, tools: list[Tool]) -> list[Tool]:
    if bool(profile.subagents) != profile.features.subagents:
        raise ConfigurationError("Set features.subagents=true with at least one named subagent")
    return [DelegateTool(name, profile, cwd, tools) for name in profile.subagents]
