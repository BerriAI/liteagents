# LiteAgents

One Python SDK for running agents with **DeepAgents, Pydantic AI, Claude Agent
SDK, Codex, and OpenCode**. Choose a harness in a profile, use the same client and
message types, and add Temporal when a run needs to survive worker failure.
Change `harness` while keeping your model, tools, MCP configuration, and
application code. LiteLLM translates model requests internally; `harness_options`
keeps the selected harness's native controls available.

Each harness runs its own agent loop. LiteAgents adds shared tools and MCP,
named subagents, operation retries, model and harness fallback, approval gates,
streaming, and durable run handles.

Follow the [getting-started guide](docs/getting-started.md) for a first agent,
application tools, MCP, and optional durable execution.

## Install

Python 3.11+; Python 3.12 is recommended. This checkout contains **0.3.0a1**.
Install it from the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install '.[deepagents]' -c constraints-tested.txt
```

This installs the first harness. A simple agent needs no Temporal or PostgreSQL
service. Replace `[deepagents]` with `[all]` to compare all harnesses, or add the
extras you need:
`deepagents`, `pydantic-ai`, `claude-sdk`, `codex`,
`mcp`, `temporal`, and `postgres`. The Claude and Codex extras supply their native
runtimes. OpenCode additionally needs `npm install -g opencode-ai@1.18.29`.

The `liteagents` name on PyPI currently serves a different package. The previous
[0.2.0 release](https://github.com/BerriAI/liteagents/releases/tag/v0.2.0) remains
available as a wheel; it does not include the portability changes in this checkout.
For a source checkout and the cookbooks, follow the
[getting-started guide](docs/getting-started.md).

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
        tools=[],
    )
    async with LiteAgentClient(options=LiteAgentOptions(profile=profile, cwd=".")) as agent:
        run = await agent.start_run("Reply with exactly READY.")
        print((await run.result()).text)

asyncio.run(main())
```

Use `agent.query(prompt)` to consume normalized text/tool messages as they
arrive. Set `profile.features.streaming = True` to receive `TextDelta` events.
Repeated `query()` calls share a conversation, with or without Temporal.
`start_run()` creates an independent job in either mode.

Omit `tools` (or use `None`) to expose registered application and MCP tools,
set `tools=[]` for no tools, or select names such as `tools=["read_file"]`.
Defaults are the same across harnesses; workspace tools are opt-in.
Application tools and MCP work without enabling retries or Temporal.

| Harness | Model configuration | Durable recovery |
| --- | --- | --- |
| `deepagents` | Shared LiteLLM model/settings | LangGraph checkpoints + operation journal |
| `pydantic-ai` | Shared LiteLLM model/settings | Model/tool operation replay |
| `claude-sdk` | Shared LiteLLM model/settings | Managed provider/MCP operation replay |
| `codex` | Shared LiteLLM model/settings | Managed provider/MCP operation replay |
| `opencode-v1` | Shared LiteLLM model/settings | Managed provider/MCP operation replay |
| `opencode-v2` | Shared LiteLLM model/settings | Managed provider/MCP operation replay |

The two OpenCode names represent upstream SDK API generations, not separate
agent engines. Both use the tested OpenCode 1.18.x server.

The same Chat Completions gateway alias works across all six selectors.
LiteLLM translates the Claude Messages and Codex Responses protocols internally.
You can also use a LiteLLM `provider/model` and its usual credentials directly;
`api_base` is required for `litellm_proxy/` aliases, not for a standard provider.
No extra translation service or configuration is needed.

Shared model settings include `temperature`, `top_p`, `max_tokens`, `stop`,
`seed`, penalties, `reasoning_effort`, and `timeout`. The model must support the
requested setting and tools; unsupported combinations fail explicitly.
Native controls stay in `harness_options`, such as DeepAgents middleware,
Claude permission settings, or Codex sandbox settings. Those controls belong to
the selected harness. See the [SDK contract](docs/sdk.md) for their scope and
conflicts with shared provider/tool configuration.

## Configure with JSON or YAML

Profiles can live in files instead of Python code. For example, `agent.yaml`:

```yaml
harness: deepagents
model: litellm_proxy/${LITEAGENTS_MODEL}
model_kwargs:
  api_base: ${LITEAGENTS_API_BASE}
  api_key: ${LITELLM_API_KEY}
tools: []
```

Load it and pass the resulting profile to the same client:

```python
profile = ProfileOptions.from_yaml("agent.yaml")
# Or: profile = ProfileOptions.from_json("agent.json")
options = LiteAgentOptions(profile=profile, cwd=".")
```

The loaders accept file paths, expand `${ENVIRONMENT_VARIABLE}` values, and
validate the same options as the Python constructor. Missing variables and
unknown fields fail before execution. See the [JSON/YAML guide](docs/profiles.md)
for equivalent JSON, MCP and Temporal configuration, and a
[runnable example](cookbook/recipes/09_profile_files.py) with both file formats.

## Try the cookbooks

The [guided recipes](cookbook/recipes/README.md) contain setup, runnable commands,
and expected results. Every recipe accepts `--harness` so you can compare behavior.

| Recipe | Demonstrates |
| --- | --- |
| [Switch harnesses](cookbook/recipes/10_harness_switch.py) | One model/profile with application tools, MCP, and follow-ups; add `--temporal` for durability |
| [Simple agent](cookbook/recipes/00_agent.py) | A first response with no tools or durability setup |
| [Quickstart](cookbook/recipes/01_quickstart.py) | File tools, live text, and conversation follow-up |
| [Application tools](cookbook/recipes/08_application_tools.py) | Register a Python tool and use stable names across harnesses |
| [JSON/YAML profiles](cookbook/recipes/09_profile_files.py) | Load the same agent from either format, with environment variables |
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
python -m pip install '.[deepagents,temporal]' -c constraints-tested.txt
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
or SDK state is retained. Each `start_run()` job is independent; consecutive
`query()` calls on one client retain completed conversation turns. Events, approval decisions,
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
