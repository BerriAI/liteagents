"""Only deterministic coordination lives here. Profiles and I/O stay on workers."""

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy


@workflow.defn(name="LiteAgentsRun")
class AgentWorkflow:
    @workflow.run
    async def run(self, request: dict[str, Any]) -> dict[str, Any]:
        return await workflow.execute_activity(
            "liteagents.run",
            {"profile_id": request["profile_id"], "prompt": request["prompt"]},
            start_to_close_timeout=timedelta(seconds=request["activity_timeout"]),
            schedule_to_close_timeout=timedelta(
                seconds=request["activity_timeout"] * request["attempts"] + 60
            ),
            heartbeat_timeout=timedelta(seconds=request["heartbeat_timeout"]),
            retry_policy=RetryPolicy(
                maximum_attempts=request["attempts"], maximum_interval=timedelta(seconds=5)
            ),
        )
