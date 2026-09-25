import argparse
import asyncio

from connection import TASK_QUEUE, connect
from temporalio.common import WorkflowIDReusePolicy


async def main() -> None:
    parser = argparse.ArgumentParser(description="Start a DeepAgents run or attach to its result.")
    parser.add_argument("action", choices=("start", "result", "status"))
    parser.add_argument("run_id")
    parser.add_argument("--prompt", default="Read order A123, validate it, then summarize.")
    args = parser.parse_args()
    client = await connect()
    if args.action == "start":
        await client.start_workflow(
            "DeepAgentWorkflow",
            args.prompt,
            id=args.run_id,
            task_queue=TASK_QUEUE,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        )
        print(f"Started {args.run_id}. This client can exit; the worker owns the run.")
    elif args.action == "result":
        print(await client.get_workflow_handle(args.run_id).result())
    else:
        print((await client.get_workflow_handle(args.run_id).describe()).status.name)


if __name__ == "__main__":
    asyncio.run(main())
