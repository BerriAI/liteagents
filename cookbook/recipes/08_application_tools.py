"""Register a Python function and keep the same application code across harnesses."""

import asyncio

from _common import parser, setup

from liteagents import run


def lookup_order(order_id: str) -> str:
    """Look up an order's payment status and total."""
    print(f"Looking up {order_id}")
    return f"Order {order_id}: paid, total USD 12"


async def main():
    args = parser(__doc__).parse_args()
    cwd, profile = setup(args, "application-tools")
    result = await run(
        "Call lookup_order for A123 and report its payment status and total.",
        profile=profile, cwd=cwd, tools=[lookup_order],
    )
    print(result.text)


if __name__ == "__main__":
    asyncio.run(main())
