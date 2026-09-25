# SDK contract

## Profiles and native loops

`LiteAgentOptions(profile=..., cwd=..., tools=..., session_id=...)` configures a
client. The profile selects one of the six harness names. Each selected harness
runs its native loop; LiteAgents manages execution boundaries and normalizes
messages. `cwd` must exist.

A profile contains `harness`, `model`, `model_kwargs`, `system_prompt`, `tools`,
`mcp_servers`, `subagents`, `features`, `recovery`, `temporal`, `harness_options`,
and `max_turns`. Unknown profile fields fail validation. Environment expansion
happens when loading YAML/JSON, including nested settings. Missing variables fail
without printing the resolved profile. Keep configuration files free of secrets.

Load a file with `ProfileOptions.from_yaml("agent.yaml")` or
`ProfileOptions.from_json("agent.json")`. Both accept a path, not raw file
contents. The [JSON/YAML profile guide](profiles.md) includes complete examples,
environment setup, and equivalent Python usage.

Python harnesses support `openai/`, `anthropic/`, and `litellm_proxy/` model names.
They also accept local native model objects through `harness_options.model_instance`.
Those objects stay on the application/worker; Temporal arguments contain only a
profile version reference and the prompt. Profiles with native objects need an
explicit, stable `temporal.profile_id`.

## Direct and durable execution

| Behavior | Direct | Temporal |
| --- | --- | --- |
| Conversation | Consecutive queries on one client share history | Each submission is independent |
| Client exit | Cancels active local work | Leaves the workflow running |
| `start_run()` | Starts work and returns a local handle | Submits once and returns a durable handle |
| `get_run(id)` | Finds a run belonging to that client | Attaches to an existing workflow |
| Worker loss | No durable recovery | Restores recorded operations/checkpoints |
| Application tools | Pass on `LiteAgentOptions` | Register on `LiteAgentWorker` |

One direct conversation permits one active query. Closing a direct query stream
cancels that run. Closing a Temporal query subscription leaves execution with the
worker; call `run.cancel()` to request cancellation explicitly.

Native Claude/Codex/OpenCode session IDs can reopen ordinary direct conversations
using `session_id`. DeepAgents and Pydantic AI direct conversations last for the
client lifetime. Native session resume alone is not checkpoint recovery.

Run handles expose the same asynchronous methods:

```python
result = await run.result()
status = await run.status()
pending = await run.approvals()
await run.approve(pending[0]["id"], allow=True)
await run.cancel()
```

`RunResult` contains `run_id`, the actual `harness`, normalized `messages`, native
`session_id`, and available `usage`. `result.text` selects the final assistant
answer. Usage retains native reports; incompatible units are not added together.

`client.capabilities` reflects the profile and application tools, including
automatic managed execution. Before creating a client, use
`get_capabilities(profile, tools=my_tools)`. Passing just a harness name returns
its ordinary native adapter capabilities. Capability reporting describes support;
the client/worker also validates configuration and loads the required runtimes.
Authentication is checked when connecting to the configured services.

## Events and streaming

`query()` yields `AssistantMessage`, `UserMessage`, and, when enabled,
`TextDelta`. A complete assistant message still follows its incremental text;
render deltas for live display and use the complete message for final content.

For shared application/workspace/MCP tools, `ToolUseBlock.name` is the public
tool name, such as `lookup` or `orders_lookup_order`, across all harnesses.
`native_name` preserves a translated native name when present. Tool-call IDs
remain unchanged for correlating results. Ordinary native built-ins keep their
native names. This mapping also applies to stored results and reattached events.

Run subscriptions expose lifecycle information and reconnectable cursors:

```python
async for event in run.events(after=last_cursor):
    last_cursor = event.cursor
    if event.kind == "text_delta":
        print(event.data["text"], end="", flush=True)
    elif event.kind == "approval_requested":
        print(event.data["tool"], event.data["arguments"])
```

Events include messages, text deltas, operation retries, model/harness fallback,
approval requests, and subagent start/event/completion. Child events carry their
scope and identity; `event.message` exposes top-level normalized messages.
Persist the cursor on the consuming side to reconnect without re-reading earlier
stored events. Subscribing does not submit another query.

Final results do not depend on a subscriber. Token events are stored outside
Temporal history. A partially emitted model response may produce replacement
text when that interrupted operation retries; completed operation responses are
replayed without another provider request. Consumers should use completed
messages/results as authoritative output.

## Tools, MCP, and subagents

Application tools implement `Tool.name`, `description`, `input_schema`, and
`async execute(input)`. Tool results must be JSON serializable for durability.
Use `operation_id()` while a tool runs to obtain an idempotency key stable across
its retries and worker recovery.

`ProfileOptions.tools` has three meanings in both direct and durable execution:

| Value | Selection |
| --- | --- |
| Omitted or `None` (`null` in YAML) | Adapter defaults, plus registered application/MCP tools |
| `[]` | No tools, including no registered tools or MCP tools |
| A list of names | Only those shared tools; an unknown name fails explicitly |

Defaults vary by adapter: DeepAgents retains its native tools, Pydantic AI exposes
registered/discovered tools, and managed CLI execution defaults to registered
tools plus `read_file`, `edit_file`, and `run_tests`. Ordinary CLI mode preserves
native defaults. Use an explicit list for comparable tool sets across harnesses.
Named subagents separately add their declared delegation tools; combining
`tools=[]` with enabled subagents is rejected. A child's empty tool list grants
no tools.

Shared workspace tools are `read_file`, `edit_file` (one unique text replacement),
and `run_tests` (an argument array, with a bounded timeout). File tools resolve
paths within `cwd`. They are conveniences, not a security sandbox: a test command
can run arbitrary programs with the worker's permissions.

All harnesses accept the same MCP schema in `profile.mcp_servers`:

```yaml
mcp_servers:
  orders:
    command: python
    args: [orders_server.py]
    allowed_tools: [lookup_order]
tools: [orders_lookup_order]
```

MCP tools receive a `<server>_` prefix. An explicit allowlist is checked against
remote names before exposure. HTTP servers use `url`, optional `headers`, and
`transport: http` or `sse`. Stdio servers use `command`, optional `args` and `env`.
The SDK owns the sessions until the run/client closes. `load_mcp_tools` also
adapts caller-owned, initialized MCP sessions. MCP 1.12 and 2.2 transports are
covered separately.

`allowed_tools: []` exposes no tools from that server; omission exposes its
catalog. Legacy `http_headers` and `enabled_tools` spellings are accepted as
aliases for `headers` and `allowed_tools`; conflicting values fail validation.
Native-only MCP controls belong in native `harness_options.config` where the
ordinary adapter supports it, and cannot be mixed with managed execution.

Named subagents are exposed as `delegate_<name>` tools. Each child runs the same
selected native harness in its own conversation, with a model override, merged
model kwargs, prompt, and explicit tool allowlist:

```yaml
features:
  subagents: true
subagents:
  auditor:
    description: Verify facts against source files.
    model: litellm_proxy/another-compatible-alias
    tools: [read_file]
    system_prompt: Report evidence and uncertainty.
```

An empty child tool list exposes no tools. Children do not inherit permission to
edit or run commands from their parent. Parent/child operations use separate
journal scopes; a completed delegation result is reused during replay. Native
built-in delegation tools are excluded from managed execution.

## Recovery and approvals

```yaml
recovery:
  retries:
    max_attempts: 3
  model_fallbacks: [litellm_proxy/backup-alias]
  harness_fallbacks: [pydantic-ai]
harness_options:
  interrupt_on:
    edit_file: true
```

`max_attempts` includes the first attempt. Eligible failures include timeouts,
connection failures, HTTP 429, and HTTP 5xx. Authentication, invalid parameters,
and denied approvals are not transient retries. Completed operations, exhausted
budgets, and approval decisions are persisted for durable runs; worker retries
do not reset them.

Model fallback preserves the native conversation at the failed model boundary.
Fallback models must use the selected harness's protocol. Harness fallback starts
the original prompt in a fresh conversation, preserving portable configuration.
It is permitted only before any tool or child delegation has begun. Native
harness-specific options do not carry to a different harness. Choose fallback
harnesses compatible with the same model settings and tools.

`interrupt_on` pauses before executing the named tool. The run exposes an approval
ID, tool name, and arguments. Answering the same decision again is idempotent;
a conflicting answer is rejected. Denial fails the run without executing that
tool. Approval waits consume the configured activity timeout; choose a timeout
that fits the intended human response window.

Cancellation stops active execution and requests native turn/process cleanup.
It cannot undo a completed external effect. As with worker interruption, effectful
tools need application idempotency or reconciliation.

## Native CLI execution modes

Ordinary direct Claude/Codex/OpenCode adapters preserve native sessions, native
tools, and native configuration. Application tools, an explicit `profile.tools`
selection (including `[]`), shared MCP servers, recovery, Temporal, approvals,
or subagents automatically select **managed execution**. Enabling retries is
unnecessary for tool adaptation. It still runs the chosen harness's own loop.

Managed execution requires an explicit gateway `api_base`. A private local
provider/MCP gateway records complete model responses before delivering tool
calls to the native process. The process still owns its loop. Only selected
managed tool definitions reach its model, and returned calls outside that set
are rejected before dispatch. Text can stream to run subscribers while the full
response is being recorded.

A replacement worker starts a fresh native conversation and supplies recorded
model/tool results at the same boundaries. Changes to semantic prompts, tool
schemas, or arguments fail replay validation. Transport IDs and Codex's generated
tool timing annotation are excluded from comparison; actual tool content remains
part of validation. Keep runtime versions, instructions, workspace paths, and
profile versions stable for in-flight runs.

Managed mode owns provider and tool configuration; raw native `config`, attached
OpenCode servers, and native tool-policy overrides cannot be combined with it.
Use the shared tool/MCP/approval fields instead. Ordinary direct mode continues
to accept the native controls described below. Shared `mcp_servers` always uses
the common schema, including when no durability is requested.

| Harness | Selected native options |
| --- | --- |
| DeepAgents | `model_instance`, `middleware`, `backend`, `skills`, `memory`, `debug` |
| Pydantic AI | `model_instance`, `tool_timeout` |
| Claude | `permission_mode`, `cli_path`, `env`, `max_budget_usd`, `timeout_seconds`, `sandbox`; direct mode also native tool policies |
| Codex | `sandbox`, `approval_mode`, `env`, `codex_bin`, `timeout_seconds`, `state_dir`; direct mode also `config` and `ephemeral` |
| OpenCode | `binary`, `env`, `timeout_seconds`, `state_dir`; direct mode also attached `base_url`, credentials, agent and native config |

Unknown adapter options fail explicitly. Native model objects and middleware are
local Python escape hatches; side effects performed outside registered operation
boundaries need their own persistence and idempotency.

## Durable storage and deployment

`TemporalOptions` defaults to localhost:7233, namespace `default`, local SQLite,
three worker recovery attempts, a 600-second activity timeout, and a 15-second
heartbeat timeout. The default run-state file is beside `checkpoint_path` with a
`.runs` suffix. Application clients and workers must resolve it to the same path.

`state_url` selects `sqlite:///absolute/path` or `postgresql://...` storage.
For shared DeepAgents workers, set both `state_url` and `checkpoint_url` to
PostgreSQL connection URLs. Other harnesses use `state_url` for their operation
journal. Clients require access to that store for results, cursors, and approvals.

SQLite workers take a local store lock. PostgreSQL workers take a session advisory
lock for each active run and check ownership while heartbeating and at operation
boundaries. Loss of ownership stops the attempt; another worker cannot dispatch
concurrently while the old owner still holds its lock. Interrupted external
effects still require application idempotency.

`max_events` bounds stored events; `max_payload_bytes` bounds each recorded
payload. `max_turns` bounds model loops. Exceeding a bound fails explicitly; store
large artifacts separately. `LiteAgentWorker.purge(older_than_days=...)` removes
up to 1,000 expired terminal runs per call, including owned graph/native
checkpoints. Cleanup first reconciles stale run status with Temporal, including
workflows that ended while no worker was alive. Active runs and workspace
artifacts remain. Temporal namespace retention is independent.

Use immutable, versioned `profile_id` values for deployments. The task queue
includes that profile identity, so a worker never consumes another registered
version's tasks. Keep old workers available while their runs drain. See
[self-hosting](self-hosting.md) for service/storage setup and TLS fields.
