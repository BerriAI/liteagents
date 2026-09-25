"""Fault injection: a synthetic primary model returns 503; the real model takes over."""

import asyncio

import httpx
from _common import parser, setup
from aiohttp import web

from liteagents import LiteAgentClient, LiteAgentOptions, RecoveryOptions


async def main():
    args = parser(__doc__).parse_args()
    cwd, profile = setup(args, "model-fallback", tools=["read_file"])
    original_model = profile.model
    upstream = profile.model_kwargs["api_base"].rstrip("/").removesuffix("/v1")
    async with httpx.AsyncClient(timeout=120) as http:

        async def inject(request):
            body = await request.json()
            if body.get("model") == "synthetic-unavailable":
                return web.json_response(
                    {"error": {"message": "Intentional cookbook outage"}}, status=503
                )
            headers = {
                k: v
                for k, v in request.headers.items()
                if k.lower()
                in (
                    "authorization",
                    "x-api-key",
                    "anthropic-version",
                    "anthropic-beta",
                    "content-type",
                )
            }
            result = await http.post(upstream + request.path, headers=headers, json=body)
            return web.Response(
                body=result.content,
                status=result.status_code,
                content_type=result.headers.get("content-type", "application/json").split(";")[0],
            )

        app = web.Application()
        app.router.add_post("/{path:.*}", inject)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        try:
            profile.model = "litellm_proxy/synthetic-unavailable"
            profile.model_kwargs["api_base"] = f"http://127.0.0.1:{runner.addresses[0][1]}/v1"
            profile.recovery = RecoveryOptions(
                retries={"max_attempts": 2}, model_fallbacks=[original_model]
            )
            async with LiteAgentClient(
                options=LiteAgentOptions(profile=profile, cwd=cwd)
            ) as client:
                run = await client.start_run("Reply with exactly: fallback-ready")
                fallbacks = 0
                async for event in run.events():
                    if event.kind in ("operation_retry", "model_fallback"):
                        print(event.kind, event.data)
                    if event.kind == "model_fallback":
                        fallbacks += 1
                result = await run.result()
                assert fallbacks and "fallback-ready" in result.text, result.text
                print(result.text)
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
