# LiteAgents

One Python client for DeepAgents, Pydantic AI, Claude Agent SDK, Codex, and
OpenCode. Each framework runs its own agent loop; LiteAgents supplies profiles,
common text/tool messages, run handles, and optional DeepAgents durability.

**v0.2 alpha implements milestones 1 and 2 of the [v2 plan](v2-proposal/IMPLEMENTATION_PLAN.md).**
All six harness names are usable. Temporal recovery is enabled for DeepAgents.
Subagent configuration, operation/model/harness fallback, durable streaming, and
approval/resume remain milestone 3 and raise explicit errors when requested.

## Install

Python 3.11+ (3.12 recommended). From this checkout:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[all,dev]'
```

For one harness, install `.[deepagents]`, `.[pydantic-ai]`, `.[claude-sdk]`, or
`.[codex]`; add `temporal` for durable DeepAgents and `mcp` for Python MCP tools.
OpenCode uses the base HTTP client plus an installed **OpenCode 1.18.29** CLI
(`npm install -g opencode-ai@1.18.29`) or an existing compatible server.
The official Codex Python SDK supplies its pinned executable. Claude's SDK
supplies its runtime; `harness_options.cli_path` can select an installed CLI.

## Run an agent

```python
import asyncio
import os
from liteagents import LiteAgentClient, LiteAgentOptions, ProfileOptions

async def main():
    profile = ProfileOptions(
        harness="deepagents",
        model="litellm_proxy/your-model-alias",
        model_kwargs={
            "api_base": os.environ["LITEAGENTS_API_BASE"],  # https://gateway/v1
            "api_key": os.environ["LITELLM_API_KEY"],
        },
        tools=["read_file", "edit_file", "run_tests"],
    )
    async with LiteAgentClient(options=LiteAgentOptions(profile=profile, cwd=".")) as agent:
        async for message in agent.query("Read README.md and summarize this project", run_id="intro"):
            print(message)
        result = await (await agent.get_run("intro")).result()
        print(result.text)
        # A second query shares this client's native conversation.
        async for message in agent.query("What would you test first?"):
            print(message)

asyncio.run(main())
```

Save the same profile as YAML/JSON and load it with `ProfileOptions.from_yaml`
or `from_json`. `${VARIABLE}` is expanded by those loaders; missing variables
are errors. Credentials and native configuration are omitted from profile reprs.

| Harness | Gateway protocol | Python tools | MCP | Persistent execution |
| --- | --- | --- | --- | --- |
| `deepagents` | Chat Completions; native Anthropic also supported | Yes | Yes | Temporal + LangGraph SQLite |
| `pydantic-ai` | Chat Completions or Anthropic | Yes | Yes | Conversation for client lifetime |
| `claude-sdk` | Anthropic Messages | Yes, via SDK MCP | Yes | Native session resume |
| `codex` | Responses | Use MCP | Yes | Native thread resume |
| `opencode-v1` | OpenAI-compatible or native provider | Use MCP | Yes | Native session resume |
| `opencode-v2` | Same OpenCode server | Use MCP | Yes | Native session resume |

OpenCode's two names represent upstream SDK API generations, **not two different
agent engines**. Both use the tested 1.18.x HTTP/SSE server adapter. Model aliases
must support the selected protocol; a Chat Completions alias alone does not
establish Responses or Anthropic compatibility.

## Compare all six

The [comparison cookbook](cookbook/compare_harnesses/README.md) includes profiles
and a small broken calculator. It gives each harness a separate workspace and
records the answer, file diff, test output, duration, and available native usage.
Missing usage stays unavailable. Your source workspace is unchanged.

```sh
python cookbook/compare_harnesses/compare.py \
  cookbook/compare_harnesses/profiles/*.yaml \
  --output /tmp/liteagents-comparison
```

Set the four environment values documented in that cookbook before running it.
These runs use your model account and execute tools in the copied workspace.

## Run durably with Temporal

Temporal Cloud hosts the Temporal service on Temporal's infrastructure. You can
also self-host the open-source service. Your workers run separately in either
case. Start locally with the persistent development server:

```sh
brew install temporal
mkdir -p .liteagents
temporal server start-dev --ip 127.0.0.1 --db-filename .liteagents/temporal.sqlite
```

The UI is at <http://localhost:8233>. In another terminal, start the SDK worker:

```sh
python cookbook/temporal/sdk_worker.py cookbook/temporal/agent.yaml --cwd .
```

Then submit and attach using the public SDK:

```sh
python cookbook/temporal/sdk_client.py cookbook/temporal/agent.yaml start demo-1 \
  --prompt 'Read README.md and summarize this project'
python cookbook/temporal/sdk_client.py cookbook/temporal/agent.yaml result demo-1
```

Set the gateway environment values first. The worker retains one local SQLite
checkpoint file; restart it with the same profile version, database, and workspace.
Completed graph steps are reused. An interrupted tool can execute again.
Temporal stores coordination and final results; LangGraph stores internal graph
checkpoints. The Temporal UI shows one agent activity, rather than an activity
for every model/tool call.

`start_run()` returns immediately. Closing the client leaves the durable run
running. `get_run()` attaches without resubmitting. Duplicate IDs are rejected
within Temporal's retention window. Each durable query is independent; direct
queries share a conversation. See [SDK details](docs/sdk.md) and the
[checkpoint explanation](cookbook/temporal/README.md).

This worker is for one host with persistent local storage. A file lock prevents
two workers from owning its checkpoint store. Distributed checkpoints, workspace
recovery after machine loss, production deployment, and mTLS are later work.

## Validation and migration

```sh
pytest -q                              # includes native Python/MCP integration
pytest -q tests/test_temporal_sdk.py   # needs localhost:7233
ruff check src/ tests/
mypy src/liteagents --ignore-missing-imports
python scripts/check_loc.py
python -m build
```

Provider tests are opt-in; see the comparison cookbook for the live test command.
The automated Temporal tests kill a worker mid-tool, restart it, and replay its
workflow history. Tests distinguish real native loops with scripted providers
from paid live-provider checks.

Existing `LiteAgentOptions(model=...)`, router, and fusion APIs remain available
for migration and have regression coverage. Use `profile=...` for the v2 API;
legacy and profile options cannot be mixed. See [legacy API](docs/legacy-api.md).
The [proposal](v2-proposal/README.md) describes the full future scope; the support
matrix here and in [SDK details](docs/sdk.md) describes the implemented behavior.
