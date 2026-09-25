# Try the SDK

These recipes use the public SDK and your model gateway. Each creates a small
workspace under `.liteagents/recipes/<recipe>/<harness>`; they do not edit your
project files. Model calls use your gateway account.

## Setup

From the repository root, with Python 3.12:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install '.[deepagents,mcp]' -c constraints-tested.txt
export LITEAGENTS_API_BASE='https://your-gateway.example/v1'
export LITELLM_API_KEY='your-key'
export LITEAGENTS_MODEL='your-chat-and-responses-model-alias'
```

This is sufficient for the DeepAgents recipes except durable runs. Add
`.[temporal]` for recipe 06. To compare every harness, install `.[all]` with the
same constraints and set `LITEAGENTS_CLAUDE_MODEL` to an Anthropic-compatible alias.
For OpenCode, also install `npm install -g opencode-ai@1.18.29`.
The Claude and Codex Python extras supply their native runtimes.

Every recipe accepts `--harness deepagents`, `pydantic-ai`, `claude-sdk`, `codex`,
`opencode-v1`, or `opencode-v2`. DeepAgents is the default. Use a model that speaks
the selected harness's protocol: Chat Completions for Python/OpenCode examples,
Responses for Codex, and Anthropic Messages for Claude. The aliases can be different.

## Recipes and expected results

| Recipe | Command | What to look for |
| --- | --- | --- |
| Simple agent | `python cookbook/recipes/00_agent.py` | Prints `READY`; no tools, Temporal, or PostgreSQL setup |
| Streaming and conversation | `python cookbook/recipes/01_quickstart.py` | Reads `facts.txt`, streams `COBALT-42`, and remembers it in a follow-up |
| Application tool | `python cookbook/recipes/08_application_tools.py` | Prints `Tool: lookup_order` and reports A123 as paid, total USD 12 |
| MCP | `python cookbook/recipes/02_mcp.py` | Launches a real local MCP server and reports order A123 as paid, total USD 12 |
| Subagents | `python cookbook/recipes/03_subagents.py` | Shows `subagent_started auditor`, then `subagent_completed auditor`; the child can only read files |
| Approval before editing | `python cookbook/recipes/04_approvals.py` | Prints proposed arguments while the file is still `status=pending`; answering `y` allows the edit |
| Operation retries | `python cookbook/recipes/05_retries.py` | Prints attempts 1, 2, 3; verifies the same idempotency key was used each time |
| Worker recovery | `python cookbook/recipes/06_durable.py worker` | Runs on a separate Temporal worker; follow the instructions below |
| Model fallback | `python cookbook/recipes/07_model_fallback.py` | Injects HTTP 503 for a synthetic primary, then prints a fallback event and `fallback-ready` from your real model |

To run the same MCP example on Codex:

```sh
python cookbook/recipes/02_mcp.py --harness codex
```

The application-tool recipe accepts the same `--harness` flag. Its public tool
name remains `lookup_order` across harnesses. These examples run directly by
default; retries are enabled explicitly in the retries/fallback recipes.

For a different child model, set `LITEAGENTS_SUBAGENT_MODEL` to a compatible gateway
alias before recipe 03. For an automated approval demo, recipe 04 accepts
`--approve`. Without it, the recipe asks before changing its sample file.

The model fallback recipe starts a temporary local fault-injection proxy. Only
the fallback model is sent to your real gateway; the synthetic outage costs no
provider requests. Harness fallback uses `recovery.harness_fallbacks` and is
allowed only before any tool begins; see the [SDK contract](../../docs/sdk.md).

## Durable run and worker crash

Start a persistent local Temporal development service in terminal 1:

```sh
brew install temporal
mkdir -p .liteagents
temporal server start-dev --ip 127.0.0.1 --db-filename .liteagents/temporal.sqlite
```

Its UI is at <http://localhost:8233>. In terminal 2, with the environment above:

```sh
python cookbook/recipes/06_durable.py worker
```

In terminal 3:

```sh
python cookbook/recipes/06_durable.py start receipt-demo-1
python cookbook/recipes/06_durable.py result receipt-demo-1
```

The submitting process exits immediately. The separate worker saves a receipt,
then spends 30 seconds in `slow_check`. The result contains `receipt-verified`.
Duplicate run IDs are rejected; use a new ID for a new run.

To test recovery, start another run. When terminal 2 prints `Slow check started`,
kill **that recipe worker's printed PID** with `kill -9 <pid>`. Restart the same
worker command with the same workspace. The result command remains attached.
It completes after the replacement retries the interrupted check. The completed
receipt operation is reused. Its application idempotency key also protects the
file write if a crash happens between the write and recording its return value.

You can repeat with `--harness codex` (or any of the six) on **both** the worker
and client commands. Use the same `--workspace` and `--delay` values in all
processes if you override their defaults. Native CLI recovery uses the managed
provider/MCP gateway; it requires the explicit API base configured above.

Other operations:

```sh
python cookbook/recipes/06_durable.py events receipt-demo-1
python cookbook/recipes/06_durable.py cancel receipt-demo-1
python cookbook/recipes/06_durable.py purge --days 30
```

Purging removes expired SDK events, operation records, results, and owned native
checkpoints. Receipt files are application artifacts and remain in the workspace.
Temporal's own namespace retention is configured separately.

For deployment beyond a local development service, use the
[self-hosting guide](../../docs/self-hosting.md). For a coding task with isolated
workspaces, diffs, and actual tests, try the
[six-harness comparison](../compare_harnesses/README.md).
