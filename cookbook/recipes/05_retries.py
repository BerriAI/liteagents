"""Inject two transient tool failures and watch the SDK retry the operation."""

import asyncio

from _common import parser, setup

from liteagents import RecoveryOptions, operation_id, run


async def main():
    args = parser(__doc__).parse_args()
    cwd, profile = setup(args, "retries")
    profile.recovery = RecoveryOptions()
    attempt_keys = []

    async def unstable_lookup() -> str:
        """Return the confirmed total for order A123."""
        attempt_keys.append(operation_id())
        attempt = len(attempt_keys)
        print("Tool attempt", attempt)
        if attempt < 3:
            raise TimeoutError("Simulated temporary failure")
        return "Confirmed total: USD 12"

    result = await run("Call unstable_lookup once and report its result.",
                       profile=profile, cwd=cwd, tools=[unstable_lookup])
    print(result.text)
    assert len(attempt_keys) == 3 and len(set(attempt_keys)) == 1
    print("Verified: three attempts used the same application idempotency key.")


if __name__ == "__main__":
    asyncio.run(main())
