"""Connection settings belong to the client/worker, outside workflow history."""

import os

from temporalio.client import Client

TASK_QUEUE = os.environ.get("TEMPORAL_TASK_QUEUE", "liteagents-deepagents-demo")


async def connect() -> Client:
    api_key = os.environ.get("TEMPORAL_API_KEY")
    return await Client.connect(
        os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),
        namespace=os.environ.get("TEMPORAL_NAMESPACE", "default"),
        api_key=api_key,
        tls=bool(api_key) or os.environ.get("TEMPORAL_TLS", "false").lower() == "true",
    )
