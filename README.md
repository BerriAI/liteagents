# LiteAgents

One Python SDK for running agents with **DeepAgents, Pydantic AI, Claude Agent
SDK, Codex, and OpenCode**. Choose a harness in a profile, use the same client and
message types, and add Temporal when a run needs to survive worker failure.

Each harness runs its own agent loop. LiteAgents adds shared tools and MCP,
named subagents, operation retries, model and harness fallback, approval gates,
streaming, and durable run handles.

## Install

Python 3.11+; Python 3.12 is recommended. From this checkout:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[all]'
```

Or install the extras you need: `deepagents`, `pydantic-ai`, `claude-sdk`, `codex`,
`mcp`, `temporal`, and `postgres`. The Claude and Codex extras supply their native
runtimes. OpenCode additionally needs `npm install -g opencode-ai@1.18.29`.

## Run an agent

Set `LITEAGENTS_API_BASE` to your gateway's `/v1` URL, `LITELLM_API_KEY` to its
key, and `LITEAGENTS_MODEL` to a compatible model alias. Then:

```python
import asyncio
import os
from liteagents import LiteAgentClient, LiteAgentOptions, ProfileOptions

async def main():
    profile = ProfileOptions(
        harness="deepagents",
        model="litellm_proxy/" + os.environ["LITEAGENTS_MODEL"],
        model_kwargs={
            "api_base": os.environ["LITEAGENTS_API_BASE"],
            "api_key": os.environ["LITELLM_API_KEY"],
        },
        tools=["read_file"],
        recovery={"retries": {"max_attempts": 3}},
    )
    async with LiteAgentClient(options=LiteAgentOptions(profile=profile, cwd=".")) as agent:
        run = await agent.start_run("Read README.md and summarize this project.")
        print((await run.result()).text)

asyncio.run(main())
```

Use `agent.query(prompt)` to consume normalized text/tool messages as they
arrive. Set `profile.features.streaming = True` to receive `TextDelta` events.
Repeated direct queries share a conversation. YAML and JSON profiles support
`${ENVIRONMENT_VARIABLE}` references through `ProfileOptions.from_yaml()` and
`from_json()`.

| Harness | Gateway protocol | Durable recovery |
| --- | --- | --- |
| `deepagents` | Chat Completions or native Anthropic | LangGraph checkpoints + operation journal |
| `pydantic-ai` | Chat Completions or Anthropic | Model/tool operation replay |
| `claude-sdk` | Anthropic Messages | Managed provider/MCP operation replay |
| `codex` | Responses | Managed provider/MCP operation replay |
| `opencode-v1` | OpenAI-compatible Chat Completions | Managed provider/MCP operation replay |
| `opencode-v2` | Same OpenCode server | Managed provider/MCP operation replay |

The two OpenCode names represent upstream SDK API generations, not separate
agent engines. Both use the tested OpenCode 1.18.x server.

For Claude, choose an Anthropic-compatible alias; for Codex, choose a
Responses-compatible alias. Native CLI recovery requires an explicit
`model_kwargs.api_base`. In managed mode, application tools, shared workspace
tools, and forwarded MCP tools pass through LiteAgents' recording gateway;
uncontrolled native tools are excluded. See the [SDK contract](docs/sdk.md) for
configuration differences and supported native options.

## Try the cookbooks

The [guided recipes](cookbook/recipes/README.md) contain setup, runnable commands,
and expected results. Every recipe accepts `--harness` so you can compare behavior.

| Recipe | Demonstrates |
| --- | --- |
| [Quickstart](cookbook/recipes/01_quickstart.py) | File tools, live text, and conversation follow-up |
| [MCP](cookbook/recipes/02_mcp.py) | Discover and call a real local MCP server |
| [Subagents](cookbook/recipes/03_subagents.py) | Named delegation, a child model, and restricted tools |
| [Approvals](cookbook/recipes/04_approvals.py) | Inspect and approve an edit before it runs |
| [Retries](cookbook/recipes/05_retries.py) | Inject tool failures and verify stable idempotency keys |
| [Durable runs](cookbook/recipes/06_durable.py) | Separate worker/client processes, reconnect, and worker crash recovery |
| [Model fallback](cookbook/recipes/07_model_fallback.py) | Inject a provider outage and switch models |

The [six-harness coding comparison](cookbook/compare_harnesses/README.md) runs a
small repair task in isolated workspaces and records diffs, tests, duration,
answers, and available usage.

## Add Temporal

Temporal Cloud hosts the Temporal service on Temporal's infrastructure. Your
workers run separately. You can also self-host the open-source service.

For local development:

```sh
brew install temporal
mkdir -p .liteagents
temporal server start-dev --ip 127.0.0.1 --db-filename .liteagents/temporal.sqlite
```

The UI is at <http://localhost:8233>. Add `TemporalOptions` to your profile:

```python
from liteagents import TemporalOptions

profile.temporal = TemporalOptions(
    profile_id="project-agent-v1",  # Bump when execution code/configuration changes.
    checkpoint_path=".liteagents/checkpoints.sqlite",
)
```

Run a worker in a separate process, registering application tools there:

```python
from liteagents.temporal import LiteAgentWorker

await LiteAgentWorker(profile=profile, cwd=".", tools=my_tools).run()
```

Clients submit or attach using the same profile version and storage settings:

```python
async with LiteAgentClient(options=LiteAgentOptions(profile=profile)) as client:
    run = await client.start_run("Do the task", run_id="task-001")
# The Temporal worker keeps running after this client exits.

async with LiteAgentClient(options=LiteAgentOptions(profile=profile)) as client:
    run = await client.get_run("task-001")
    print(await run.status())
    print((await run.result()).text)
```

Attaching never resubmits. Duplicate run IDs are rejected while Temporal history
or SDK state is retained. Each durable query is independent. Events, approval decisions,
operation results, and final output live in SQLite or PostgreSQL; Temporal stores
coordination and a small result reference.

Completed recorded operations are reused after a crash. An interrupted external
effect can run again: tools that charge, publish, or write to external systems
should use `operation_id()` as their application idempotency key. Checkpointing
does not restore a lost working directory. Preserve the workspace and stores
when moving workers.

Use the [durable cookbook](cookbook/recipes/README.md#durable-run-and-worker-crash)
for a complete crash demo, or the [self-hosting guide](docs/self-hosting.md) for
PostgreSQL, shared workers, TLS, retention, and deployment examples.

## Validation and migration

Tests exercise real native harness loops, actual MCP transports, worker-process
kills, replay, streaming, approvals, retries, fallback boundaries, shared
PostgreSQL storage, and ownership. Paid live-provider tests are opt-in.
[Validation details](docs/validation.md) distinguish scripted providers from
live checks and list the supported runtime versions.

```sh
pip install -e '.[all,postgres,dev]'
pytest -q
ruff check src tests
mypy src/liteagents --ignore-missing-imports
python scripts/check_loc.py
python -m build
```

V2 replaces the public v1 implementation. Existing loop/router/fusion applications
can temporarily use `liteagents.legacy` with the `legacy` extra. Follow the
[migration guide](docs/migration.md) to move to profiles and native harnesses.
The original [proposal](v2-proposal/README.md) is retained as design history.
