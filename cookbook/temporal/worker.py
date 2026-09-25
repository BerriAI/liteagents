import asyncio

from activities import run_deepagent
from connection import TASK_QUEUE, connect
from temporalio.worker import Worker
from workflows import DeepAgentWorkflow


async def main() -> None:
    client = await connect()
    print(f"Worker polling {TASK_QUEUE}", flush=True)
    await Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[DeepAgentWorkflow],
        activities=[run_deepagent],
        max_concurrent_activities=1,
    ).run()


if __name__ == "__main__":
    asyncio.run(main())
