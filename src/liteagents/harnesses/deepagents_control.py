"""Native LangChain middleware for checkpointed model/tool operation boundaries."""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import messages_from_dict, messages_to_dict
from langgraph.types import Command

from ..runtime.control import CURRENT, retryable, stable
from ..storage.store import digest


def encode_response(response: Any) -> dict[str, Any]:
    return {
        "messages": messages_to_dict(response.result),
        "structured": response.structured_response,
    }


def decode_response(value: dict[str, Any]) -> Any:
    return ModelResponse(
        result=messages_from_dict(value["messages"]), structured_response=value["structured"]
    )


def encode_tool(value: Any) -> dict[str, Any]:
    if isinstance(value, Command):
        update = dict(value.update or {})
        if "messages" in update:
            update["messages"] = messages_to_dict(update["messages"])
        return {"command": True, "update": update, "goto": value.goto, "graph": value.graph}
    return {"messages": messages_to_dict([value])}


def decode_tool(value: dict[str, Any]) -> Any:
    if value.get("command"):
        update = dict(value["update"])
        if "messages" in update:
            update["messages"] = messages_from_dict(update["messages"])
        return Command(update=update, goto=value["goto"], graph=value["graph"])
    return messages_from_dict(value["messages"])[0]


class OperationMiddleware(AgentMiddleware):
    def __init__(self, models: list[tuple[str, Any]]):
        self.models = models

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        control = CURRENT.get()
        if control is None:
            return await handler(request)
        messages = stable(messages_to_dict(request.messages))
        for index, (name, model) in enumerate(self.models):
            arguments = {"model": name, "messages": messages}

            async def invoke(model=model):
                return await handler(request.override(model=model))

            try:
                return await control.call(
                    "model",
                    digest(arguments),
                    arguments,
                    invoke,
                    encode=encode_response,
                    decode=decode_response,
                )
            except Exception as exc:
                if index == len(self.models) - 1 or not retryable(exc):
                    raise
                await control.emit("model_fallback", previous=name, model=self.models[index + 1][0])
        raise AssertionError("unreachable")

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        control = CURRENT.get()
        if control is None:
            return await handler(request)
        tool = request.tool_call
        arguments = {"name": tool["name"], "input": tool["args"]}

        async def invoke():
            return await handler(request)

        return await control.call(
            "tool", tool["id"], arguments, invoke, encode=encode_tool, decode=decode_tool
        )
