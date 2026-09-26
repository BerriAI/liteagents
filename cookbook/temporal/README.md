# Start LiteAgents v2 with Temporal and DeepAgents

For the current SDK in your browser, use the
[durable-runs Colab notebook](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/06_durable.ipynb).
It starts a demo Temporal service and separate worker, supports reconnecting to a run,
and includes an optional worker crash/restart experiment.

The public SDK now implements profiles, six harness names, and durable DeepAgents
runs. Start with the [SDK quickstart](../../README.md) using `sdk_worker.py`,
`sdk_client.py`, and `agent.yaml` in this directory.

The original standalone scripts below remain a no-credentials crash-recovery
exercise: they run a real DeepAgents graph with a scripted model. The public SDK
has its own equivalent subprocess-kill and workflow replay tests in
`tests/test_temporal_sdk.py`.

## What runs where

```text
Application / LiteAgents client
    | starts a workflow, receives a run ID, may disconnect
    v
Temporal service                       Your Python worker
    | records workflow history             | runs DeepAgents
    | schedules tasks and retries -------->| calls models and tools
    |                                      | saves LangGraph checkpoints
    v                                      v
Temporal database                      Agent checkpoint database
```

- **Workflow:** the durable coordination for one agent run.
- **Activity:** code run by your worker; here, it drives the DeepAgents graph.
- **Worker:** your Python process, including the harness, tools, and model credentials.
- **Task queue:** the name connecting clients to workers that can execute their runs.
- **Namespace:** an isolation/retention boundary in Temporal; local development uses `default`.

Temporal Cloud hosts the Temporal service for you. You still operate your workers
and, for this DeepAgents design, the agent checkpoint store. The open-source
Temporal service can also run entirely in your own infrastructure. The same
worker/client architecture works with either hosting choice.

Temporal stores workflow inputs and results, which may contain prompt/output
data. Model credentials in this example stay in the worker environment.

## Start locally

Use Python 3.12 for this example. From this directory:

```sh
brew install temporal
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -r requirements.txt
mkdir -p .state
```

Terminal 1 — Temporal, including its browser UI:

```sh
temporal server start-dev --ip 127.0.0.1 --db-filename .state/temporal.sqlite
```

The `--db-filename` is intentional: without it, the development server's state
is in memory and disappears on server restart. Always restart from this directory
with the same file. The API is `localhost:7233`; open <http://localhost:8233> for
the UI. This single-process development server is not a production deployment.

Terminal 2 — the worker:

```sh
.venv/bin/python worker.py
```

Terminal 3 — start a run, then reconnect to it:

```sh
.venv/bin/python client.py start deepagents-demo-1
.venv/bin/python client.py status deepagents-demo-1
.venv/bin/python client.py result deepagents-demo-1
```

Expected result after about 10 seconds:

```text
Order A123: 2 notebooks, USD 12. Validation passed.
```

`start` returns immediately. `result` attaches to the existing run and waits for
its result; it does not submit the prompt again. Starting the same ID a second
time is rejected, including after completion. Use a new ID for a new run.

## Prove worker crash recovery

Keep the Temporal server running. The verification creates its own isolated task
queue, checkpoint file, and worker processes; it does not kill other workers:

```sh
.venv/bin/python verify_recovery.py
```

The graph makes a model decision, reads an order, makes another model decision,
and enters a slow validation tool. The verification then kills its first worker
and launches a replacement. It checks that:

1. The completed order lookup and both preceding model decisions execute once.
2. The interrupted validation tool is attempted twice.
3. Temporal's second activity attempt resumes the saved graph and finishes.

Evidence is written to `.state/recovery-*/worker-*.log`. Inspect the corresponding
workflow in the UI. To test server persistence, stop and restart the development
server using the same database, then use `client.py result <recovery-run-id>`.
Completed results remain retrievable within the namespace's retention period.

Verified locally on September 25, 2026 with Temporal CLI 1.9.1 / server 1.32.0:
the worker crash test passed for `recovery-8a5403e4809b`, and its completed result
was successfully retrieved after stopping and restarting the Temporal server.

## Try a real model through LiteLLM

Restart the worker with the following environment settings. Supply credentials
in your own shell; do not put them in workflow inputs or committed profiles.

```sh
export DEEPAGENTS_MODEL="your-gateway-model-alias"
export OPENAI_API_BASE="https://your-litellm-gateway.example/v1"
export OPENAI_API_KEY="your-litellm-virtual-key"
.venv/bin/python worker.py
```

Then start a new run ID. This uses `ChatOpenAI` against the gateway's
OpenAI-compatible endpoint. The model must support tool calls. For a direct
provider account, omit `OPENAI_API_BASE` and use a supported model name.
The two demo tools only read fixed data and wait; this is not yet a coding agent.
The automated crash test always uses the scripted model, even if these variables
are set. Real-provider behavior and paid model calls need separate validation.

## What the checkpoint guarantee actually is

This starter has **two durability layers**:

- Temporal persists run orchestration, retries, and the final activity result.
- DeepAgents/LangGraph persists graph state, model messages, and completed tool
  results in SQLite, using `durability="sync"` before advancing to the next step.

Temporal retries `run_deepagent`. That activity loads the same graph thread and
resumes with `ainvoke(None, ...)`; it does not append the original prompt again.
The thread key includes both the workflow ID and Temporal execution ID, so a
new execution cannot accidentally pick up an older execution's checkpoint.
If the graph finished but Temporal did not record the activity completion, the
next attempt returns the saved final result.

The Temporal UI shows one retried agent activity, not one activity per internal
model/tool call. This is an explicit difference from the current v2 proposal's
blanket promise that Temporal itself records every harness's model/tool results.

An interrupted operation can run again. Neither Temporal nor graph checkpoints
make external side effects exactly-once. A tool that creates a PR, charges a
card, or changes a remote system needs a stable idempotency key and reconciliation
when its effect succeeded but the checkpoint did not. Checkpoints also do not
restore a worker's local filesystem: coding workspaces need durable storage and
appropriate ownership when workers move between machines.

This is a single-machine recovery proof. Production workers need shared
checkpoint storage and protection against overlapping/zombie attempts writing
the same run. Human approvals, cancellation delivery, token streaming,
subagent recovery, long-run history management, workflow versioning, and all
fallback behavior still need explicit implementation and tests.

## Move to self-hosting

Start with the local development server even if you intend to self-host; Temporal
recommends this path. A production deployment adds:

1. The Temporal service, deployed using its supported Docker/Kubernetes or manual
   deployment setup, with its persistence and visibility schemas in a supported
   database such as PostgreSQL.
2. Your LiteAgents workers, deployed independently and polling the service.
3. A shared DeepAgents checkpoint store, such as LangGraph's `AsyncPostgresSaver`.
   Keep its tables/schemas separate from Temporal's databases; Temporal's
   internal storage is not an application checkpoint API.
4. Durable agent workspaces/artifact storage where the tools require files to
   survive machine failure, plus deployment-specific access and operational setup.

There is no need to start with a Kubernetes cluster just to learn Temporal.
When connecting elsewhere, configure the service endpoint and namespace:

```sh
export TEMPORAL_ADDRESS="your-temporal-host:7233"
export TEMPORAL_NAMESPACE="your-namespace"
export TEMPORAL_TASK_QUEUE="liteagents-deepagents-demo"
```

This starter also accepts `TEMPORAL_API_KEY` and enables TLS when one is present,
which supports Temporal Cloud API-key connections. Use the exact namespace and
endpoint from the Cloud console. Self-hosted mTLS configurations need certificate
options added to `connection.py`. This example does not deploy a production server.

## Current SDK

The earlier sections retain the original single-machine DeepAgents proof. The
public SDK now implements operation recovery across all six harnesses, shared
PostgreSQL state, approval/resume, retries/fallbacks, and subagents. Start with the
[guided durable recipe](../recipes/README.md#durable-run-and-worker-crash) for the
current API, the [SDK contract](../../docs/sdk.md) for guarantees, and the
[self-hosting guide](../../docs/self-hosting.md) for the tested Compose deployment.

## Sources

- [Temporal local development](https://docs.temporal.io/develop/run-a-development-server)
- [Self-hosted Temporal](https://docs.temporal.io/self-hosted-guide)
- [Python client and Temporal Cloud connections](https://docs.temporal.io/develop/python/client/temporal-client)
- [LangGraph checkpoints and durability modes](https://docs.langchain.com/oss/python/langgraph/checkpointers)
- [Pydantic AI's Temporal integration](https://ai.pydantic.dev/durable_execution/temporal/)
