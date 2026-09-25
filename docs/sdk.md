# SDK contract and supported configuration

## Runs and conversations

`LiteAgentClient` is an async context manager. `query(prompt, run_id=...)` yields
`AssistantMessage`, `UserMessage` (tool results), and optional `TextDelta`.
`ToolUseBlock.id` correlates with `ToolResultBlock.tool_use_id`. Completed
assistant messages include full text even when deltas were emitted; display
one or the other to avoid duplicated text. `RunResult.text` returns the final
assistant response; `messages` retains intermediate tool exchanges.

Direct runs execute while `query()` is consumed. One client owns one native
conversation. Concurrent queries are rejected. Run IDs are unique within that
client; handles cannot be recovered from another client. `LocalRun.status` is
an attribute; `await LocalRun.result()` returns or raises the original failure.
Wrap a partially consumed query in `contextlib.aclosing` to interrupt it and
release resources. Cancelling a query cancels that direct invocation.

Claude, Codex, and OpenCode results expose a native `session_id`. Pass it as
`LiteAgentOptions(session_id=..., cwd=...)` with the same native storage and
workspace to reopen a conversation. This resumes history for a **new** query;
it does not attach to or recover an interrupted operation. DeepAgents and
Pydantic AI direct history only lasts for the client; they reject session IDs.

Temporal `start_run()` submits a detached workflow. `query()` submits and waits
for the final messages; no token streaming is provided on this path.
`get_run(id)` only attaches. `await run.status()` queries Temporal. Closing a
client or cancelling its wait leaves the workflow running. New durable queries
use independent graph threads. Public durable cancellation is deferred.

Duplicate Temporal IDs are rejected while the workflow remains in namespace
retention. A run handle's result may contain prompts, tool outputs, and model
responses; Temporal and checkpoint databases should be treated as application
data stores. Large results over 1.5 MB are rejected; use external artifacts for
large tool outputs.

## Models, limits, and native options

`model_kwargs` is deliberately validated per adapter. The SDK never changes
process-global credentials. Environment references expand only when loading a
YAML/JSON file; direct Python profiles accept ordinary values/native objects.

| Harness | Supported common model settings | Selected native options |
| --- | --- | --- |
| DeepAgents | Native LangChain model fields; `api_base` → `base_url` | `model_instance`, `middleware`, `backend`, `skills`, `memory`, `debug` |
| Pydantic AI | OpenAI/Anthropic model settings; `api_base`, `api_key`; `reasoning_effort` → `openai_reasoning_effort` | `model_instance`, `tool_timeout` |
| Claude | `api_base`, `api_key`, `reasoning_effort` | `permission_mode`, `allowed_tools`, `disallowed_tools`, `cli_path`, `env`, `max_budget_usd`, `setting_sources`, `timeout_seconds`, `stderr`, `sandbox` |
| Codex | `api_base`, `api_key`, `reasoning_effort` | `sandbox`, `approval_mode`, `config`, `env`, `codex_bin`, `timeout_seconds`, `ephemeral`, `state_dir` |
| OpenCode | `api_base`, `api_key`, `temperature`, `top_p`, `reasoning_effort` | `base_url`, `binary`, `env`, `config`, `agent`, `timeout_seconds`, `username`, `password`, `state_dir` |

Python harnesses accept `openai/model`, `anthropic/model`, and
`litellm_proxy/alias`; DeepAgents can also use LangChain's provider factory.
Other Pydantic AI providers use an explicitly constructed `model_instance`.
The application owns supplied model objects; the adapter closes clients it
creates. Claude needs an Anthropic-compatible model. Codex needs Responses.
OpenCode can use its native providers or the configured OpenAI-compatible gateway.

`max_turns` is optional; an unspecified limit uses 20 model calls/steps where supported. DeepAgents uses
native model-call middleware and Pydantic AI uses request limits. Claude and
owned OpenCode servers receive their native turn/step limits. Codex exposes no
model-call budget; explicitly setting `max_turns` raises an error. Bound it with
`timeout_seconds` (default 300). Attached OpenCode servers must configure their
own models, MCP, agent options, and limits; the SDK never changes shared server
configuration. Supported OpenCode server versions are 1.18.x, tested on 1.18.29.

Codex state defaults to `cwd/.liteagents/codex`. Set `state_dir` to a dedicated
persistent directory to share native sessions between application clients.
This scopes its personal configuration, plugins, authentication and history to
the SDK. Authenticate through the profile or configure that directory for your
native use case. `approval_mode` defaults to `deny_all`; native actions needing
permission can fail. `auto_review` delegates decisions to Codex's native reviewer.
Human approval/resume through LiteAgents is not implemented.

Owned OpenCode servers use `cwd/.liteagents/<harness>` for their XDG data,
configuration, cache, and state directories. `state_dir` overrides that location.
A lock prevents simultaneous servers from opening the same native SQLite store;
use separate directories for independent servers or `base_url` to attach to one
shared server. Resume a native session with the same harness and state directory.
The SDK uses dedicated ports so concurrently starting servers do not race for
OpenCode's default port.

Do not combine legacy `LiteAgentOptions(model=..., stream=..., system=...)` or
nondefault legacy token/turn limits with `profile=...`.

## Tools and MCP

For DeepAgents/Pydantic AI, register `Tool` objects with
`LiteAgentOptions(tools=[...])`, then select their names in `profile.tools`.
With an empty selection, all registered tools are included. Shared workspace
implementations are available for `read_file`, `edit_file`, and `run_tests`.
The file tools reject paths outside the workspace; the test tool executes an
argument array with a 120-second timeout. It is not an OS sandbox.

DeepAgents includes its native toolset, excluding native delegation, when no selection is supplied. Claude
maps these three names to Read, Edit, and Bash. OpenCode maps them to read, edit,
and bash. Native shell tools have their native behavior and permissions.
Codex uses its native toolset; it rejects `profile.tools` and Python tool objects.
Use MCP for custom Codex/OpenCode tools. Unknown adapter options fail explicitly;
`config` itself is a native configuration escape hatch validated by that runtime.

Python profiles support managed MCP stdio, Streamable HTTP, and SSE sessions:

```yaml
mcp_servers:
  evidence:
    command: python3
    args: ["/absolute/path/to/server.py"]
    allowed_tools: [read_memory]
tools: [evidence_read_memory]
```

For a remote server, replace `command`/`args` with `url`, optional `headers`, and
`transport: http` (default) or `sse`. Python tools use `server_tool` names.
Connections stay open throughout the client and close with it. MCP 1.12 and
2.2 transport APIs are covered. Duplicate tool names are rejected.

Claude uses native MCP names such as `mcp__evidence__read_memory`. It accepts
`command/args/env` or `url/headers/transport`; restrict names through
`profile.tools`. Python tools become an SDK MCP server named `liteagents`.

Codex MCP configuration uses native fields: `command/args/env` or `url`,
`http_headers`, `bearer_token_env_var`, `enabled_tools`, `disabled_tools`,
`startup_timeout_sec`, `tool_timeout_sec`, `enabled`, and per-tool `tools`.
Explicitly authorize a known tool when it should run without an approval dialog:

```yaml
mcp_servers:
  evidence:
    command: python3
    args: ["/absolute/path/to/server.py"]
    enabled_tools: [read_memory]
    tools:
      read_memory:
        approval_mode: approve
```

OpenCode accepts `command/args/env` or `url/headers`; native server config can
supply more specialized settings. Unsupported shared MCP fields are rejected
rather than silently ignored. Native MCP permissions still apply.

## Durable workers

`LiteAgentWorker(profile=..., tools=[...], cwd=...).run()` owns execution.
Register custom tools on the worker, not the submitting client. The client
sends only the prompt, an opaque profile version, and execution timeouts to
Temporal. Profiles/credentials/native Python objects stay out of workflow
arguments. Tool/model outputs and error messages can still contain task data.

Set `temporal.profile_id` to a stable, versioned identifier when profiles contain
native objects or clients omit worker credentials. **Change the ID when model,
tool implementations, instructions, or native configuration change.** Otherwise
the default is a digest of the resolved profile. Effective task queues append
an ID digest so different profiles do not consume each other's tasks.

Worker recovery uses the same graph thread, keyed by workflow ID and Temporal
execution ID. Synchronous LangGraph checkpoints retain completed steps, including
a completed final result if the activity completion must be reported again.
`worker_recovery_attempts` (default 3, including the first) is an activity retry
budget, not the proposal's per-model/tool retry policy. Configuration errors are
nonretryable. Interrupted effects may repeat: external mutation tools need their
own idempotency or reconciliation.

The local SQLite store has one worker owner and one concurrent activity. Keep
the database and workspace on persistent local storage. This is not distributed
worker fencing or workspace restoration. Cloud and self-hosted Temporal use the
same client/worker architecture, but shared checkpoint storage, mTLS, long-run
retention, and production worker versioning remain later deployment work.

## Supported dependency baseline

Verified with Python 3.12.14, DeepAgents 0.7.19, LangChain 1.4.2,
langchain-openai 1.6.6, Pydantic AI 2.50.0, Claude Agent SDK 0.2.159,
openai-codex 0.157.0, OpenCode 1.18.29, MCP 2.2.0, and Temporal Python 1.33.0
against local Temporal server 1.32.0. Optional extras constrain the native API
families. CI covers the minimum MCP 1.12 transport separately.

The full proposal's subagents, per-operation retry/fallback, approval/resume,
and reconnectable durable event streams remain milestone 3. Native runtime
escape hatches are not a promise of portable behavior or crash recovery.
