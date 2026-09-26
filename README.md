# LiteAgents

Switch between **DeepAgents, Pydantic AI, Claude Agent SDK, Codex, and OpenCode**
through one Python SDK. Pick a harness and model, run your agent, then change
`harness` to try another framework with the same application code.

[Read our blog post](https://docs.litellm.ai/blog/liteagents-sdk) ·
[Website](https://www.litellm.ai/liteagents) ·
[Getting started](docs/getting-started.md)

## Quickstart

[![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/00_agent.ipynb)

Install LiteAgents and the two harnesses used below (Python 3.11+):

```sh
python -m pip install "liteagents[pydantic-ai,claude-sdk] @ https://github.com/BerriAI/liteagents/releases/download/v0.3.0a4/liteagents-0.3.0a4-py3-none-any.whl"
export OPENAI_API_KEY="your-openai-key"
```

This preview uses a GitHub release wheel; the PyPI name currently belongs to a
different package. No checkout, gateway, or Temporal service is needed.

Save this as `agent.py` and run `python agent.py`:

```python
import asyncio
from liteagents import ProfileOptions, run

profile = ProfileOptions(
    harness="pydantic-ai",
    model="openai/gpt-5.4-mini",
)
prompt = "Explain what an agent harness does in one sentence."

async def main():
    result = await run(prompt, profile=profile)
    print(result.text)

asyncio.run(main())
```

You'll see the agent's answer. In Colab, use `await main()` instead of
`asyncio.run(main())`.

## Switch the harness

Change `harness="pydantic-ai"` to `harness="claude-sdk"` and run the same example.
Claude Agent SDK uses the same OpenAI model and key. Your result still has
`result.text`.

Or, inside `main()` after your first run (or directly in Colab):

```python
profile.harness = "claude-sdk"
result = await run(prompt, profile=profile)
print(result.text)
```

Each call starts an independent task. For follow-up turns with shared history,
use [a conversation client](#read-responses-and-continue-conversations).

Install the harness integrations you want in the same environment. The extras
are `deepagents`, `pydantic-ai`, `claude-sdk`, `codex`, `opencode-v1`, and
`opencode-v2`; `[all]` installs all Python integrations. Claude and Codex include
their runtimes; OpenCode also needs `npm install -g opencode-ai@1.18.29`.
Compatible installed dependencies are reused. Switching does not download a
harness or move an existing native session between frameworks.

## Choose a model provider

The same agent code works with these model settings. Set the matching key and
change only `profile.model`; LiteLLM's Python SDK handles provider translation.

| Provider | Example `model` | Key environment variable |
| --- | --- | --- |
| OpenAI | `openai/gpt-5.4-mini` | `OPENAI_API_KEY` |
| Anthropic | `anthropic/claude-sonnet-4-6` | `ANTHROPIC_API_KEY` |
| OpenRouter | `openrouter/anthropic/claude-sonnet-4.6` | `OPENROUTER_API_KEY` |
| Google Gemini | `gemini/gemini-2.5-flash` | `GEMINI_API_KEY` |
| Groq | `groq/llama-3.3-70b-versatile` | `GROQ_API_KEY` |
| Mistral | `mistral/mistral-small-latest` | `MISTRAL_API_KEY` |
| DeepSeek | `deepseek/deepseek-chat` | `DEEPSEEK_API_KEY` |
| Together AI | `together_ai/meta-llama/Llama-3.3-70B-Instruct-Turbo` | `TOGETHERAI_API_KEY` |
| xAI | `xai/grok-3-mini` | `XAI_API_KEY` |

For example, set `ANTHROPIC_API_KEY` and use
`profile.model = "anthropic/claude-sonnet-4-6"` with the same harness.
Choose a model enabled for your account that supports the tools/settings you use.

[Model setup](docs/models.md) includes copyable key setup for these providers,
Azure OpenAI, Amazon Bedrock, Vertex AI, local Ollama, and optional gateway access.

## How it works

`your app → LiteAgents → selected harness → LiteLLM → model provider`

The harness owns the agent loop. LiteAgents translates shared configuration,
tools, and MCP for it, then normalizes the output. LiteLLM's Python SDK handles
model-provider translation. Harness-specific controls remain available through
`profile.harness_options`.

`run()` returns a `RunResult`: `result.text` is the final answer,
`result.messages` holds the normalized messages, and `result.usage` preserves
available native usage reports.

The streaming and conversation interface is **modeled after the Claude Agent
SDK**, with `query()`, `LiteAgentClient`, `AssistantMessage`, and `TextBlock`.
These types come from `liteagents` and work across harnesses. They are Python
dataclasses, not OpenAI `choices` responses. Selecting Pydantic AI does not
require the Claude Agent SDK; the quickstart installs both to demonstrate switching.

## Read responses and continue conversations

Keep one client open for follow-ups. Consecutive `query()` calls share history
in direct and Temporal execution. For example, inside an async function:

```python
from liteagents import AssistantMessage, LiteAgentClient, LiteAgentOptions, TextBlock

async with LiteAgentClient(options=LiteAgentOptions(profile=profile)) as agent:
    for prompt in ["Remember the code COBALT-42.", "What code did I give you?"]:
        async for message in agent.query(prompt):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        print(block.text)
```

Use `run()` when you want a final result, `query()` when you want messages as
execution proceeds, and `client.start_run()` when you need a handle for
approvals, status, or cancellation. [The SDK reference](docs/sdk.md) covers
streaming text, tool events, and reconnecting to durable runs.

## Optional: use a LiteLLM Gateway

Set your gateway's exact model alias, endpoint, and key in the same profile:

```python
from getpass import getpass

profile = ProfileOptions(
    harness="pydantic-ai",
    model="my-model",
    model_kwargs={
        "api_base": "https://your-gateway.example/v1",
        "api_key": getpass("Gateway API key: "),
    },
)
```

Pass this profile to `run()`. The alias is sent unchanged, including slashes;
the gateway selects the provider. Your application uses its gateway key instead
of provider keys. [Model setup](docs/models.md) explains both connection options.

## Shared tools and native controls

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
Use your gateway's exact model alias with `model_kwargs.api_base`, or a LiteLLM
`provider/model` and its usual credentials without a gateway endpoint.
Existing `litellm_proxy/alias` configurations remain supported.
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
harness: pydantic-ai
model: openai/gpt-5.4-mini
```

Load it and pass the resulting profile to `run()`:

```python
profile = ProfileOptions.from_yaml("agent.yaml")
# Or: profile = ProfileOptions.from_json("agent.json")
result = await run(prompt, profile=profile)
```

The loaders accept file paths, expand `${ENVIRONMENT_VARIABLE}` values, and
validate the same options as the Python constructor. Missing variables and
unknown fields fail before execution. See the [JSON/YAML guide](docs/profiles.md)
for equivalent JSON, MCP and Temporal configuration, and a
[runnable example](cookbook/recipes/09_profile_files.py) with both file formats.

## Try the cookbooks

The [Colab cookbooks](cookbook/README.md) run in your browser with no checkout.
Start with [your first agent](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/00_agent.ipynb),
[switching harnesses](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/10_harness_switch.ipynb),
or [comparing coding runs](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/compare_harnesses/compare.ipynb).

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
from liteagents import LiteAgentClient, LiteAgentOptions

async with LiteAgentClient(options=LiteAgentOptions(profile=profile)) as client:
    handle = await client.start_run("Do the task", run_id="task-001")
# The Temporal worker keeps running after this client exits.

async with LiteAgentClient(options=LiteAgentOptions(profile=profile)) as client:
    handle = await client.get_run("task-001")
    print(await handle.status())
    print((await handle.result()).text)
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
