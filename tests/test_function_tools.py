import asyncio
import json
from typing import Annotated, Literal

import pytest
from pydantic import BaseModel, Field, ValidationError

from liteagents import ConfigurationError, operation_id
from liteagents.function_tools import adapt_tools
from liteagents.runtime.control import OPERATION_ID
from tests.test_python_harnesses import Lookup


class Order(BaseModel):
    code: str


async def test_function_schema_validation_defaults_and_typed_nested_values():
    calls = []

    async def lookup(order: Order, count: Annotated[int, Field(gt=0)] = 1,
                     *, currency: Literal["USD", "EUR"] = "USD") -> dict:
        """Look up an order total."""
        assert isinstance(order, Order)
        calls.append((order.code, count, currency))
        return {"order": order, "total": count * 12, "currency": currency}

    tool = adapt_tools([lookup])[0]
    assert tool.name == "lookup" and tool.description == "Look up an order total."
    assert tool.input_schema["required"] == ["order"]
    assert json.loads(await tool.execute({"order": {"code": "A123"}})) == {
        "order": {"code": "A123"}, "total": 12, "currency": "USD",
    }
    for invalid in ({"count": 0}, {"currency": "INVALID"}, {"unexpected": True}):
        with pytest.raises(ValidationError):
            await tool.execute({"order": {"code": "A123"}, **invalid})
    assert calls == [("A123", 1, "USD")], "Invalid inputs must not execute the application"


async def test_sync_function_runs_off_event_loop_with_operation_context():
    def lookup() -> str:
        """Return the current operation key."""
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        return operation_id()

    token = OPERATION_ID.set("stable-operation")
    try:
        assert await adapt_tools([lookup])[0].execute({}) == "stable-operation"
    finally:
        OPERATION_ID.reset(token)


def test_existing_tools_remain_unchanged_and_duplicates_fail_early():
    tool = Lookup()
    assert adapt_tools([tool]) == [tool]
    with pytest.raises(ConfigurationError, match="Duplicate.*lookup"):
        adapt_tools([tool, tool])
    with pytest.raises(TypeError, match="typed Python functions"):
        adapt_tools([object()])


def test_unsupported_function_signatures_fail_before_execution():
    def missing_hint(order):
        return order

    def variadic(*args: str):
        return args

    def positional(order: str, /):
        return order

    def generator(order: str):
        yield order

    def private_parameter(_order: str):
        return _order

    for function, message in [(missing_hint, "type hint"), (variadic, "named parameters"),
                              (positional, "named parameters"), (generator, "not yield"),
                              (private_parameter, "underscore")]:
        with pytest.raises(ConfigurationError, match=message):
            adapt_tools([function])


async def test_function_errors_propagate_to_recovery():
    async def lookup() -> str:
        raise TimeoutError("retry this operation")

    with pytest.raises(TimeoutError, match="retry this operation"):
        await adapt_tools([lookup])[0].execute({})
