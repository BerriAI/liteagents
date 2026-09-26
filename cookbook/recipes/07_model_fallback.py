"""Simulate a primary-model outage and recover with your configured model."""

import asyncio
from unittest.mock import patch

import litellm
from _common import parser, setup

from liteagents import RecoveryOptions, run


async def main():
    args = parser(__doc__).parse_args()
    cwd, profile = setup(args, "model-fallback")
    fallback = profile.model
    profile.model = "openai/cookbook-unavailable"
    profile.recovery = RecoveryOptions(retries={"max_attempts": 1}, model_fallbacks=[fallback])
    real_completion = litellm.acompletion

    async def simulate_outage(*args, model, **kwargs):
        if model.endswith("/cookbook-unavailable"):
            print("Primary model unavailable; trying the fallback.")
            raise litellm.ServiceUnavailableError(
                message="Intentional cookbook outage", llm_provider="openai", model=model,
            )
        return await real_completion(*args, model=model, **kwargs)

    with patch("litellm.acompletion", side_effect=simulate_outage):
        result = await run("Reply with exactly: fallback-ready", profile=profile, cwd=cwd)
    print(result.text)


if __name__ == "__main__":
    asyncio.run(main())
