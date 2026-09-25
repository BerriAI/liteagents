from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, aclosing
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware
from langgraph.checkpoint.memory import InMemorySaver

from ..errors import ConfigurationError, HarnessError, UnsupportedFeatureError
from ..runtime.control import CURRENT
from ..runtime.delegation import delegate_tools
from ..runtime.tooling import load_servers, select_tools
from ..types import AgentEvent, TextDelta
from .base import HarnessAdapter
from .deepagents_control import OperationMiddleware
from .langchain_support import build_model, convert_message, text_content, wrap_tool


class ToolSelection(AgentMiddleware):
    def __init__(self, names: set[str] | None):
        self.names = names

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        selected = [
            tool
            for tool in request.tools
            if (
                getattr(tool, "name", None) in self.names
                if self.names is not None
                else getattr(tool, "name", None) != "task"
            )
        ]
        return await handler(request.override(tools=selected))

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        name = request.tool_call["name"]
        if (self.names is not None and name not in self.names) or (
            self.names is None and name == "task"
        ):
            raise PermissionError(f"Tool {name!r} is outside this agent tool selection")
        return await handler(request)


class DeepAgentsAdapter(HarnessAdapter):
    allowed_options = frozenset(
        {
            "model_instance",
            "middleware",
            "backend",
            "skills",
            "memory",
            "debug",
            "interrupt_on",
            "fallback_model_instances",
            "subagent_model_instances",
        }
    )

    def validate(self) -> None:
        super().validate()
        if self.resume_session:
            raise UnsupportedFeatureError(
                "Direct DeepAgents history lasts for one client; use Temporal for persistent runs"
            )

    async def open(self) -> None:
        self.stack = AsyncExitStack()
        registered = self.tools + await load_servers(self.profile, self.stack)
        # Explicit tools use the shared implementations. With no selection DeepAgents
        # retains its native toolset, plus all discovered MCP tools.
        delegates = delegate_tools(self.profile, self.cwd, self.tools)
        registered += delegates
        names = self.tool_allowlist
        if names is None and self.profile.tools is not None:
            names = set(self.profile.tools)
        if names is not None:
            names = names | {t.name for t in delegates}
        selected = (
            select_tools(
                self.profile.model_copy(update={"tools": sorted(names)}), registered, self.cwd
            )
            if names is not None
            else registered
        )

        options = {
            key: value
            for key, value in self.profile.harness_options.items()
            if key
            not in (
                "model_instance",
                "interrupt_on",
                "fallback_model_instances",
                "subagent_model_instances",
            )
        }
        middleware = list(options.pop("middleware", []))
        limits = {
            "thread_limit" if self.profile.temporal else "run_limit": (self.profile.max_turns or 20)
        }
        middleware.append(ModelCallLimitMiddleware(**limits, exit_behavior="error"))
        middleware.append(ToolSelection(names))
        checkpointer: Any
        if self.profile.temporal:
            control = CURRENT.get()
            if control is not None and control.store is not None:
                await control.put(
                    "checkpoint:" + self.session_id,
                    {"kind": "deepagents", "session": self.session_id},
                )
            if self.profile.temporal.checkpoint_url:
                from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

                checkpointer = await self.stack.enter_async_context(
                    AsyncPostgresSaver.from_conn_string(self.profile.temporal.checkpoint_url)
                )
                await checkpointer.setup()
            else:
                from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

                path = Path(self.profile.temporal.checkpoint_path)
                if not path.is_absolute():
                    path = self.cwd / path
                path.parent.mkdir(parents=True, exist_ok=True)
                checkpointer = await self.stack.enter_async_context(
                    AsyncSqliteSaver.from_conn_string(str(path))
                )
        else:
            checkpointer = InMemorySaver()
        options.setdefault("backend", FilesystemBackend(root_dir=str(self.cwd), virtual_mode=True))
        primary = await build_model(self.profile, self.stack)
        models = [(self.profile.model, primary)]
        fallbacks = self.profile.recovery.model_fallbacks if self.profile.recovery else []
        supplied = self.profile.harness_options.get("fallback_model_instances", [])
        for index, name in enumerate(fallbacks):
            native = dict(self.profile.harness_options)
            native.pop("model_instance", None)
            if index < len(supplied):
                native["model_instance"] = supplied[index]
            alternative = self.profile.model_copy(update={"model": name, "harness_options": native})
            models.append((name, await build_model(alternative, self.stack)))
        middleware.append(OperationMiddleware(models))
        self.graph: Any = create_deep_agent(
            model=primary,
            tools=[wrap_tool(tool) for tool in selected],
            system_prompt=self.profile.system_prompt,
            middleware=middleware,
            checkpointer=checkpointer,
            **options,
        )
        self.native_session_id = self.session_id

    async def close(self) -> None:
        if hasattr(self, "stack"):
            await self.stack.aclose()

    async def query(
        self, prompt: str, *, run_id: str, resume: bool = False
    ) -> AsyncGenerator[AgentEvent, None]:
        config = {
            "configurable": {"thread_id": self.session_id},
            "recursion_limit": (self.profile.max_turns or 20) * 4 + 4,
        }
        before = await self.graph.aget_state(config)
        seen = set()
        if before.values:
            for message in before.values.get("messages", []):
                if resume:
                    event = convert_message(message, self.profile.model)
                    if event is not None:
                        yield event
                seen.add(message.id)
        if resume and before.values and not before.next:
            return
        inputs = (
            None
            if resume and before.values
            else {"messages": [{"role": "user", "content": prompt}]}
        )
        try:
            stream = self.graph.astream(
                inputs,
                config=config,
                stream_mode=["updates", "messages"]
                if self.profile.features.streaming
                else ["updates"],
                durability="sync",
            )
            async with aclosing(stream):
                async for mode, data in stream:
                    if mode == "messages":
                        if self.profile.features.streaming:
                            text = text_content(data[0].content)
                            if text:
                                yield TextDelta(text, self.profile.model)
                    else:
                        if "__interrupt__" in data:
                            raise UnsupportedFeatureError(
                                "Native graph interrupts are unsupported; use harness_options.interrupt_on"
                            )
                        for update in data.values():
                            if not isinstance(update, dict):
                                continue
                            messages = update.get("messages", [])
                            if not isinstance(messages, list):
                                messages = [messages]
                            for message in messages:
                                if not hasattr(message, "type") or (
                                    message.id and message.id in seen
                                ):
                                    continue
                                event = convert_message(message, self.profile.model)
                                if message.id:
                                    seen.add(message.id)
                                if event is not None:
                                    yield event
        except (ConfigurationError, UnsupportedFeatureError):
            raise
        except Exception as exc:
            raise HarnessError(f"DeepAgents run failed: {type(exc).__name__}: {exc}") from exc
