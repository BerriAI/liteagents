"""An actual DeepAgents graph; the default model is scripted and costs nothing."""

import asyncio
import os
from typing import Any

from deepagents import create_deep_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool


@tool
async def read_order(order_id: str) -> str:
    """Read an example order from a fixed, local fixture."""
    print(f"TOOL read_order {order_id}", flush=True)
    return f"Order {order_id}: 2 notebooks; total USD 12."


@tool
async def validate_order(order_id: str) -> str:
    """Validate an example order; deliberately slow enough to test worker failure."""
    print(f"TOOL_START validate_order {order_id}", flush=True)
    await asyncio.sleep(10)
    print(f"TOOL_DONE validate_order {order_id}", flush=True)
    return f"Order {order_id} passed validation."


class ScriptedModel(BaseChatModel):
    """Choose the next tool from persisted messages, with no process-local cursor."""

    @property
    def _llm_type(self) -> str:
        return "liteagents-temporal-demo"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedModel":
        return self

    def _generate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        completed = {m.name for m in messages if isinstance(m, ToolMessage)}
        next_tool = next(
            (name for name in ("read_order", "validate_order") if name not in completed), None
        )
        print(f"MODEL {next_tool or 'final'}", flush=True)
        if next_tool:
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": next_tool,
                        "args": {"order_id": "A123"},
                        "id": next_tool,
                    }
                ],
            )
        else:
            message = AIMessage(content="Order A123: 2 notebooks, USD 12. Validation passed.")
        return ChatResult(generations=[ChatGeneration(message=message)])


def build_agent(checkpointer: Any) -> Any:
    model_name = os.environ.get("DEEPAGENTS_MODEL")
    if model_name:
        from langchain_openai import ChatOpenAI

        # OPENAI_API_KEY / OPENAI_API_BASE may point at a LiteLLM gateway.
        model = ChatOpenAI(model=model_name, temperature=0, max_retries=0)
    else:
        model = ScriptedModel()
    return create_deep_agent(
        model=model,
        tools=[read_order, validate_order],
        system_prompt=(
            "Read order A123, then validate it, then summarize the result. "
            "Call read_order and validate_order in separate, sequential turns. "
            "Use only those two tools."
        ),
        checkpointer=checkpointer,
    )
