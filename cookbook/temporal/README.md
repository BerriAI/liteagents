# Add Temporal to LiteAgents

Use the [Temporal Colab](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/06_durable.ipynb)
for a complete walkthrough: submit a task, stop its worker, restart it, and
retrieve the same run. It includes the local test service and cleanup.

Temporal is optional. Your model, credentials, tools, and harness configuration
stay on the same profile. A separate worker executes durable runs.

## Run the SDK example locally

From the repository root, install the selected harness and Temporal integration:

```sh
python -m pip install '.[pydantic-ai,temporal]' -c constraints-tested.txt
export OPENAI_API_KEY='your-openai-key'
brew install temporal
mkdir -p .liteagents
```

Start the development service in one terminal:

```sh
temporal server start-dev --ip 127.0.0.1 --db-filename .liteagents/temporal.sqlite
```

Start a worker in another terminal with the same provider credentials:

```sh
python cookbook/temporal/sdk_worker.py cookbook/temporal/agent.yaml
```

Submit a task from a third terminal:

```sh
python cookbook/temporal/sdk_client.py cookbook/temporal/agent.yaml start demo-1 \
  --prompt 'Explain what an agent harness does in one sentence.'
python cookbook/temporal/sdk_client.py cookbook/temporal/agent.yaml result demo-1
```

`start` prints a run ID and disconnects. `result` attaches without submitting
again. Use a new ID for a new job. Run these commands from the same directory so
clients and workers share the checkpoint path.

The profile in `agent.yaml` uses Pydantic AI and a direct OpenAI model. Change
`harness` to another installed integration for subsequent jobs. Install its
extra on the worker; application calls and provider setup stay the same. For a
deployed profile change, use a new `profile_id` and let existing jobs finish on
their original workers.

## What Temporal adds

The Temporal service tracks jobs and schedules retries. Your worker runs the
chosen harness and records completed model/tool operations in the SDK state
store. After worker loss, completed operations are reused and interrupted work
is retried. External writes still need application idempotency; `operation_id()`
provides a stable key while a tool executes.

The local setup uses SQLite. A deployment can use self-hosted Temporal or
Temporal Cloud with your own workers and shared PostgreSQL state. See
[self-hosting](../../docs/self-hosting.md) and the
[SDK recovery contract](../../docs/sdk.md#direct-and-durable-execution).

The [terminal crash-recovery recipe](../recipes/README.md#durable-run-and-worker-crash)
shows the full worker lifecycle with a receipt tool. The original standalone
DeepAgents experiment is preserved as a [historical checkpoint proof](checkpoint-proof.md).
