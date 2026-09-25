"""Temporal retries the activity; LangGraph resumes its persisted graph state."""

import asyncio
import os
from contextlib import suppress
from pathlib import Path

from agent_definition import build_agent
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from temporalio import activity


async def heartbeat() -> None:
    while True:
        activity.heartbeat()
        await asyncio.sleep(1)


@activity.defn
async def run_deepagent(prompt: str) -> str:
    info = activity.info()
    # Include the Temporal execution ID: a reset/new execution must not load stale state.
    thread_id = f"{info.workflow_id}:{info.workflow_run_id}"
    print(f"ACTIVITY attempt={info.attempt} workflow={info.workflow_id}", flush=True)
    db_path = Path(
        os.environ.get(
            "LITEAGENTS_CHECKPOINT_DB", str(Path(__file__).parent / ".state/deepagents.sqlite")
        )
    )
    db_path.parent.mkdir(parents=True, exist_ok=True)
    beating = asyncio.create_task(heartbeat())
    try:
        async with AsyncSqliteSaver.from_conn_string(str(db_path)) as checkpointer:
            agent = build_agent(checkpointer)
            config = {"configurable": {"thread_id": thread_id}}
            state = await agent.aget_state(config)
            if state.values and not state.next:
                print("CHECKPOINT already complete", flush=True)
                result = state.values
            else:
                resuming = bool(state.values)
                print(f"CHECKPOINT {'resume' if resuming else 'new'}", flush=True)
                inputs = None if resuming else {"messages": [{"role": "user", "content": prompt}]}
                result = await agent.ainvoke(inputs, config=config, durability="sync")
            return result["messages"][-1].text
    finally:
        beating.cancel()
        with suppress(asyncio.CancelledError):
            await beating
