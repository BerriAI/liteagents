# Temporal and self-hosting

Temporal has two deployment choices. **Temporal Cloud** hosts the Temporal
service; **self-hosting** runs that open-source service on your infrastructure.
In both cases your LiteAgents workers execute models and tools, and your
SQLite/PostgreSQL store retains SDK results, events, approvals, and operation
records. Temporal's database does not replace that SDK store or your workspace.

## Local development

The [durable cookbook](../cookbook/recipes/README.md#durable-run-and-worker-crash)
uses `temporal server start-dev` with a persistent SQLite file. Keep the Temporal
file, SDK checkpoint/state files, profile version, and working directory when
restarting. The default `localhost:7233` service and `localhost:8233` UI match the
Temporal CLI defaults.

This is the easiest starting point and the worker-crash tests use it. Use
PostgreSQL for workers on more than one host.

## Self-hosted PostgreSQL example

Install a Docker engine and Docker Compose before running these commands.

[deployment/compose.yaml](../deployment/compose.yaml) starts PostgreSQL, Temporal
Server 1.29.7, and its UI. It uses persistent named volumes and loopback-only
published ports, separate from the default local development ports.

```sh
docker compose -f deployment/compose.yaml up -d postgres temporal temporal-ui
docker compose -f deployment/compose.yaml ps
```

Endpoints are Temporal `localhost:17233`, UI <http://localhost:18233>, and
PostgreSQL `localhost:15433`. The example uses a development password; set
`POSTGRES_PASSWORD` before creating its volumes for a deployment you intend to
keep. Use a URL-safe password or percent-encode it in connection URLs.

After setting your gateway environment as in the recipes, start the SDK worker:

```sh
docker compose -f deployment/compose.yaml --profile worker up -d --build worker
docker compose -f deployment/compose.yaml logs -f worker
```

The image contains all Python harness extras, tested dependency constraints, and
OpenCode 1.18.29. The supplied profile runs DeepAgents with read-only file tools.
It registers profile version `self-hosted-reader-v1`, uses PostgreSQL for both
LangGraph and SDK state, and mounts a persistent workspace. Adapt the profile and
worker entry point when adding application tools or choosing another harness.

Prepare a sample file in that workspace:

```sh
docker compose -f deployment/compose.yaml exec worker \
  python -c "from pathlib import Path; Path('/workspace/hello.txt').write_text('self-hosted-verified')"
```

From a Python environment with `liteagents[temporal,postgres]`, export the client
connection URL and submit through the public SDK helper:

```sh
export LITEAGENTS_CLIENT_STATE_URL='postgresql://temporal:local-development-only@localhost:15433/liteagents'
python cookbook/temporal/sdk_client.py deployment/client.yaml start self-hosted-demo-1 \
  --prompt 'Read hello.txt and report its contents'
python cookbook/temporal/sdk_client.py deployment/client.yaml result self-hosted-demo-1
```

Use your configured password if it differs. Expect `self-hosted-verified` in the
answer. The application profile only needs the registered profile ID, Temporal
connection, and SDK storage connection; model credentials and application tool
objects remain on the worker.

`docker compose ... down` stops these services while retaining named volumes.
Deleting volumes deletes their stored data. Back up databases and workspace
artifacts according to your deployment's recovery requirements.

This example is a single-host deployment starting point. For a production
Temporal cluster, use the official [deployment documentation](https://docs.temporal.io/self-hosted-guide)
for service topology, database capacity, authentication, schema upgrades,
observability, and high availability. The UI and frontend should be exposed only
through your chosen authenticated/private network boundary.

## Shared worker configuration

For DeepAgents:

```yaml
temporal:
  address: temporal.internal:7233
  namespace: agents
  profile_id: order-agent-v3
  state_url: ${LITEAGENTS_STATE_URL}
  checkpoint_url: ${LITEAGENTS_STATE_URL}
  max_concurrent_runs: 4
  activity_timeout_seconds: 1800
  heartbeat_timeout_seconds: 15
  worker_recovery_attempts: 3
  max_events: 20000
  max_payload_bytes: 8000000
  retention_days: 30
```

Pydantic AI and the native CLI harnesses use `state_url` for their operation
journal; `checkpoint_url` is specific to DeepAgents graph storage. Clients need
access to the same run-state store. Use private database connectivity and
appropriate database permissions for those clients.

Workers share PostgreSQL state and acquire per-run advisory locks. SQLite uses a
local worker lock and is intended for one host. A lock prevents two live owners
from dispatching the same run; it does not make an interrupted remote API effect
exactly once. Pass `operation_id()` to APIs that support idempotency keys, or
reconcile external state in the tool implementation.

Native CLI operation recovery requires the configured gateway and managed MCP
boundary. Keep compatible CLI/SDK versions, unchanged instructions, and the same
workspace path on replacement workers. Mount or restore workspace files before
accepting work there. A fresh empty volume cannot restore edited project files
from Temporal history.

## Temporal Cloud and TLS

For Temporal Cloud with an API key:

```yaml
temporal:
  address: your-namespace.account.tmprl.cloud:7233
  namespace: your-namespace.account
  api_key: ${TEMPORAL_API_KEY}
  tls: true
  profile_id: order-agent-v3
  state_url: ${LITEAGENTS_STATE_URL}
  checkpoint_url: ${LITEAGENTS_STATE_URL}
```

API-key connections enable TLS. For a private certificate authority or mutual
TLS, supply file paths available on the connecting client/worker:

```yaml
temporal:
  address: temporal.internal:7233
  namespace: agents
  tls: true
  tls_server_root_ca: /run/secrets/temporal-ca.pem
  tls_client_cert: /run/secrets/temporal-client.pem
  tls_client_key: /run/secrets/temporal-client.key
  tls_server_name: temporal.internal
```

Client certificate and key must be supplied together. PostgreSQL TLS is
configured in its connection URL, for example `sslmode=verify-full` and the
appropriate CA setting. Temporal service credentials, model credentials, and
database credentials serve different connections; do not put them in prompts.

## Versions, limits, and retention

Use an immutable `profile_id` for a deployed version, and bump it when changing
execution configuration, tool implementations, or runtime versions. Each version
uses a separate task queue. Keep workers for old versions while their runs finish.
Do not replace code underneath an in-flight run while reusing its version label.

`constraints-tested.txt` records the dependency versions used for acceptance.
The package metadata permits compatible ranges. Qualify upgrades with the
worker-crash and native contract tests before moving active workloads.

Events and operation results stay outside Temporal history. Large tool outputs
should be external artifacts with references. Event and payload bounds fail
explicitly rather than silently discarding records. Choose activity timeouts
that include human approval waits.

Run retention as an explicit maintenance command:

```python
removed = await worker.purge(older_than_days=30)
```

Each call removes up to 1,000 expired terminal runs for that worker's profile,
including owned graph/native checkpoints. Repeat until it returns zero to drain a
larger backlog. It retains active runs and application workspace artifacts.
Configure the Temporal namespace's workflow-history retention separately. Once
SDK results expire, `run.result()` reports that the state is unavailable.

The PostgreSQL storage and shared-worker paths are exercised by integration
tests. The Compose services and worker image are also tested end to end. See
[validation](validation.md) for the exact checks performed on the release branch.
