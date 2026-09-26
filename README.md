# LiteAgents

Switch between **DeepAgents, Pydantic AI, Claude Agent SDK, Codex, and OpenCode**
through one Python SDK. Change `harness` while keeping your model, tools, MCP
configuration, and application code.

**The public interface is modeled after the Claude Agent SDK:** an async
`query()` iterator, a conversation client, and typed messages such as
`AssistantMessage` and `TextBlock`. Import these from `liteagents`, whichever
harness you select. Each harness still runs its own agent loop; selecting
DeepAgents runs DeepAgents without requiring the Claude Agent SDK.

A simple agent runs locally. Temporal and PostgreSQL are optional.

Follow the [getting-started guide](docs/getting-started.md) for a first agent,
application tools, MCP, and optional durable execution.

## How it works

| Part | Responsibility |
| --- | --- |
| Your application | Supplies a prompt, profile, and tools; reads LiteAgents messages. |
| LiteAgents | Adapts shared configuration, tools, and MCP to the selected harness and normalizes its output. Exposes harness-specific settings through `harness_options`. |
| Selected harness | Owns the agent loop: builds context, calls the model and tools, and decides when the task is complete. |
| LiteLLM | Translates model requests to the chosen provider or your LiteLLM gateway. A gateway is optional. |
| Temporal, when enabled | Coordinates durable runs on a worker. LiteAgents stores checkpoints, operation results, and events in SQLite or PostgreSQL. |

For example, a DeepAgents run with an OpenAI model follows
`your app → LiteAgents → DeepAgents → LiteLLM → OpenAI`. Change the harness to
Pydantic AI and LiteAgents adapts the same shared configuration to Pydantic AI.
Your response-handling code stays the same. Native options remain specific to
the selected harness, and the model must support the requested settings and tools.

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

The `liteagents` name on PyPI currently serves a different package. To install
without cloning, use the wheel and tested constraints from the
[0.3.0a1 preview release](https://github.com/BerriAI/liteagents/releases/tag/v0.3.0a1).
For a source checkout and the cookbooks, follow the
[getting-started guide](docs/getting-started.md).

## Run an agent

Set `OPENAI_API_KEY` for this example, then run:

```python
import asyncio
from liteagents import (
    AssistantMessage, LiteAgentOptions, ProfileOptions, TextBlock, query,
)

profile = ProfileOptions(
    harness="deepagents",  # Change to "pydantic-ai", "claude-sdk", or "codex".
    model="openai/gpt-5.4-mini",
    tools=[],
)
options = LiteAgentOptions(profile=profile)

async def main():
    async for message in query(prompt="Reply with exactly READY.", options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(block.text)

asyncio.run(main())
```

Install each harness integration you want to use. After changing `harness`, run
the same code again. LiteAgents selects the installed harness; it does not
download one during a query or migrate a conversation between harnesses.

To use a LiteLLM gateway instead of a provider directly, set
`model="litellm_proxy/your-model-alias"` and pass its `/v1` URL and key in
`model_kwargs={"api_base": ..., "api_key": ...}`. See the
[gateway example](docs/getting-started.md) for environment-variable configuration.

## Read responses and continue conversations

`query()` yields **LiteAgents Python dataclasses modeled after the Claude Agent
SDK's messages**. These are the same across harnesses and model providers.
Even when the model is OpenAI, read `message.content` as shown above rather than
`response.choices[0].message`.

| Type | Content |
| --- | --- |
| `AssistantMessage` | A `content` list of `TextBlock` and/or `ToolUseBlock`, plus `model` and available `usage`. Read text from `block.text`; tool calls expose `name`, `input`, and `id`. |
| `UserMessage` | Input text or content blocks, including tool results as `ToolResultBlock`. |
| `TextDelta` | Incremental `text` when `profile.features.streaming = True`. A complete `AssistantMessage` follows; avoid displaying both as separate answers. |

For a conversation, keep one `LiteAgentClient` open and call `agent.query()` for
each turn. Consecutive queries on that client share history in direct and Temporal
execution. The top-level `query()` helper above creates a fresh client per call.

For an independent job, use `start_run()`. If you only need the final answer,
read `result.text`:

```python
from liteagents import LiteAgentClient

# Inside an async function, using the options defined above:
async with LiteAgentClient(options=options) as agent:
    run = await agent.start_run("Reply with exactly READY.")
    result = await run.result()
    print(result.text)
    # result.messages contains the normalized messages for this run.
```

See the [events and streaming guide](docs/sdk.md#events-and-streaming) for
tool-call names, run events, and reconnectable subscriptions.

## Switch harnesses and keep shared settings

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
