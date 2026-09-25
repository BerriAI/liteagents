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
from ..runtime.tooling import load_servers, select_tools
from ..types import AgentEvent, TextDelta
from .base import HarnessAdapter
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


class DeepAgentsAdapter(HarnessAdapter):
    allowed_options = frozenset(
        {"model_instance", "middleware", "backend", "skills", "memory", "debug"}
    )

    def validate(self) -> None:
        super().validate()
        if self.profile.temporal and self.profile.features.streaming:
            raise UnsupportedFeatureError("Live Temporal streaming is a milestone 3 feature")
        if self.resume_session:
            raise UnsupportedFeatureError(
                "Direct DeepAgents history lasts for one client; use Temporal for persistent runs"
            )

    async def open(self) -> None:
        self.stack = AsyncExitStack()
        registered = self.tools + await load_servers(self.profile, self.stack)
        # Explicit tools use the shared implementations. With no selection DeepAgents
        # retains its native toolset, plus all discovered MCP tools.
        selected = (
            select_tools(self.profile, registered, self.cwd) if self.profile.tools else registered
        )
        options = {
            key: value
            for key, value in self.profile.harness_options.items()
            if key != "model_instance"
        }
        middleware = list(options.pop("middleware", []))
        limits = {
            "thread_limit" if self.profile.temporal else "run_limit": (self.profile.max_turns or 20)
        }
        middleware.append(ModelCallLimitMiddleware(**limits, exit_behavior="error"))
        middleware.append(ToolSelection(set(self.profile.tools) if self.profile.tools else None))
        checkpointer: Any
        if self.profile.temporal:
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
        self.graph: Any = create_deep_agent(
            model=await build_model(self.profile, self.stack),
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
                                "Approval/resume is a milestone 3 feature"
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
