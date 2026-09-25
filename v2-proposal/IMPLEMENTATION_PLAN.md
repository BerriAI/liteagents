# LiteAgents v2 implementation plan

Prepared September 25, 2026 against the complete v2 README at `539a6e2`.
The architecture below records the original four-milestone build plan.
The [SDK contract](../docs/sdk.md), [quickstart](../README.md), and
[validation report](../docs/validation.md) describe the current implementation.

## Product target

Ship an installable Python SDK that lets an application select any of the six
proposed harnesses, configure it through a profile, consume common messages,
and optionally execute durable runs through Temporal. DeepAgents is the first
integration; the user's release target includes all six for comparison.

Keep the README's public entry points:

- `ProfileOptions`, including YAML/JSON and environment references.
- `LiteAgentOptions(profile=...)`.
- `LiteAgentClient.query(prompt, run_id=...)`.
- `LiteAgentClient.get_run(run_id)` and `run.result()`.
- `LiteAgentWorker(profile=...).run()`.

Each harness owns its agent loop. LiteAgents owns configuration translation,
run identity, normalized events, execution lifecycle, and verified recovery
behavior. It does not implement another agent loop around each framework.

## Every README feature and its implementation

| README feature | Work required | Acceptance condition |
| --- | --- | --- |
| Same client and `query()` | Dispatch through an adapter registry; direct and Temporal execution paths | Same consumer code works for all six harness profiles |
| Common text/tool messages | Normalize text, deltas, tool starts/results, errors, and completion; retain native IDs | Correct ordering, tool correlation, and no duplicated displayed text |
| Python/YAML/JSON profiles | Typed validation, explicit defaults, environment references, missing-variable errors | Equivalent profiles behave the same; unsupported settings fail before work starts |
| Harness and model selection | Translate names and model parameters using harness-specific provider integrations | Supported model settings take effect; incompatible combinations produce actionable errors |
| LiteLLM gateway | Configure the protocol each harness consumes; forward supported per-run overrides | Verify actual request parameters, alias resolution, and provider behavior against a test gateway |
| Tools and MCP | Resolve registered tool names and native tools; manage harness-appropriate MCP sessions | A real tool invocation and MCP invocation work on every compatible adapter |
| Subagents | Translate description, model, model settings, and tool restrictions into native delegation | Verify delegation, allowed tools, attribution, and supported per-subagent model overrides |
| Feature toggles | Validate requested features against adapter capabilities | A toggle cannot silently do nothing |
| Temporal worker | Register versioned profiles/tools; start workflows; dispatch/recover native runs | Client exit leaves the worker running; a new client attaches by ID |
| Retry limits | Classify errors, count eligible operation attempts, coordinate nested SDK retries | Configured attempt count includes the first attempt and is demonstrably enforced |
| Model fallback | Preserve native conversation and tool definitions when changing model | Fallback works without discarding prior results or repeating completed tools |
| Harness fallback | Restart the original prompt on another compatible harness only at the permitted point | No automatic harness switch after any tool has begun |
| Native `harness_options` | Validate/forward supported options; resolve executable controls on workers | Middleware and native controls actually execute; unknown fields fail clearly |
| Interrupt before editing | Persist an approval request and provide a way to answer it | An edit does not execute before approval; the wait survives worker/client restart |

The README's example models and tool names are configuration examples, not a
promise that every provider and every tool implementation fits every harness.
Model parameters, tools, MCP transports, and subagent configuration need an
explicit compatibility matrix.

## Package structure

```text
src/liteagents/
  agent.py                 # Public client/options; choose an execution path
  types.py                 # Existing public message types + lifecycle events
  profiles.py              # Validation and profile loading
  runs.py                  # Attach, result, status, cancellation, event cursors
  tools.py                 # Named application tools and registration
  mcp.py                   # Shared MCP configuration and adaptable clients
  harnesses/
    base.py                # Adapter interface, capabilities, registry
    deepagents.py
    pydantic_ai.py
    claude_sdk.py
    codex.py
    opencode/              # Shared transport; version-specific mappings
  runtime/
    direct.py              # Local invocation without Temporal
    events.py              # Event normalization, ordering, and subscriptions
    recovery.py            # Retry/fallback decisions and error classification
    store.py               # Run/session metadata and reconnectable event log
  temporal/
    worker.py
    workflows.py
    activities.py
```

Split files by responsibility as they approach the repository's 500-line limit.
Keep shared dependencies small and expose optional harness/Temporal extras.
DeepAgents currently requires Python 3.11+; recommend Python 3.11+ for v2 rather
than retaining the current unconditional Python 3.10 promise.

Preserve useful existing message types, MCP code, and tested provider settings.
Replace the custom loop as the client implementation when v2 adapters take over.
The old JEV router/fusion APIs are not specified in the v2 proposal: explicitly
decide whether to migrate them as extensions or document their removal in the
breaking release. Update `AGENTS.md` to describe the authorized v2 architecture.

## Adapter implementation choices

| Harness | Proposed integration | Recovery work |
| --- | --- | --- |
| DeepAgents | Python `create_deep_agent`, native middleware/tools/subagents, normalized graph events | Temporal orchestration plus synchronous LangGraph checkpoints; replace local SQLite with shared Postgres for distributed workers |
| Pydantic AI | Python `Agent`, native tools/toolsets, its official Temporal integration | Model/tool activity boundaries already exist upstream; adapt retries, events, and result handling |
| Claude Agent SDK | `ClaudeSDKClient`, native messages, tools/MCP and subagent definitions | Persist session identity and session storage; verify interrupted operation behavior and any pre-tool interception before enabling fallback |
| Codex | Official Python `openai-codex` / `AsyncCodex`, pinned runtime | Persist native thread/turn identity and native state; verify reconnection, interrupted turns, tools, and recovery behavior |
| OpenCode v1 | Python HTTP/event-stream client against the intended pinned server/API | Persist session identity; reconcile a running/completed prompt after disconnect before deciding whether to submit anything |
| OpenCode v2 | Share OpenCode lifecycle/transport, add API-version-specific mappings | Run the same compatibility and interruption tests independently |

Current upstream `@opencode-ai/sdk` exports both its default API and `/v2` in
one package. This suggests the proposal's two names may refer to SDK API
generations, not independent harness engines. Confirm and pin that mapping;
do not manufacture two separate implementations if a shared adapter is correct.

The official Codex docs now describe a stable Python SDK with a pinned local
runtime, so a custom Node bridge is not the default implementation choice.

The current implementation uses LangGraph checkpoints for DeepAgents and a
shared operation journal for all six harnesses. Native CLI recovery runs behind
a managed provider/MCP gateway. See [validation](../docs/validation.md) for actual
worker-crash, approval, subagent, MCP, and provider checks. The integration table
above records the initial plan, not the final recovery architecture.

## Durable execution contract

Use one LiteAgents run ID for one submitted query and a distinct native session
identity for conversation continuation. A client, Temporal workflow, harness
session, and model tool call are different things; persist their association.
Define repeated `query()` calls explicitly before implementation: retain one
conversation per client/session while each submitted query has its own run ID.
Serialize concurrent queries to a session unless an adapter explicitly supports
another safe behavior.

Persist the selected profile/version, adapter/runtime version, native session
identity, checkpoint location, current status, final result/error, and event
cursor. A reconnect only observes the existing run. Reject duplicate submission
IDs by default, including after completion. A retry must not silently load a
different profile because a worker's environment/configuration changed.

Temporal workflow code performs deterministic coordination. Model calls,
filesystem access, MCP sessions, and native subprocesses belong in activities
or the framework's supported durable integration. Keep credentials and Python
objects out of workflow arguments/history: send a versioned profile reference
and resolve tools, middleware, and secrets in the worker. Direct Python execution
can still accept native objects as described in the README.

For DeepAgents, expose checkpoint-store configuration in addition to the
Temporal service settings. For session-based harnesses, explicitly configure
where native state and the working directory live. Neither a Temporal database
nor a graph checkpoint restores files lost with a worker machine.

Capability reporting should distinguish:

- **Durable orchestration:** the run remains recorded and the worker can retry.
- **Native session recovery:** conversation history can be reopened, with tested
  behavior for interrupted turns.
- **Operation recovery:** completed operations are reused at proven boundaries.

If a requested recovery guarantee cannot be met, reject that configuration.
The README's unconditional per-model/tool Temporal checkpointing promise must
either be implemented and proven for each adapter or narrowed explicitly. Silent
degradation to rerunning the original prompt is not an implementation of it.

## Streaming, retries, fallback, and approvals

Workers should publish events independently of the application consuming them.
Use ordered event IDs and a run event store (SQLite locally, Postgres for a shared
deployment) so reconnecting consumers can continue from a cursor. Retain completed
messages separately from transient token deltas. Do not write one Temporal history
event per token. Keep final result retrieval independent of whether anybody was
watching the stream. Large outputs/artifacts should be referenced outside history.

Separate worker recovery from eligible model/tool retries. The starter retries
one agent activity; that alone does not implement the README's per-operation
`max_attempts`. Where a native SDK exposes retries, coordinate them with LiteAgents
to prevent three outer attempts multiplying into many hidden provider attempts.
Do not retry invalid credentials/configuration as transient failures.

For model fallback, carry the current native conversation/tools into the supported
replacement model. For harness fallback, preserve the original prompt and start
a new native session. Persist that a tool is about to start before dispatching it
using a reliable adapter hook. If a harness cannot expose that boundary, disable
automatic harness fallback rather than infer safety from an incomplete event log.

Interrupted tools can execute again. Tools with external effects need stable
idempotency keys or reconciliation. Distributed workers also need ownership/
fencing so a timed-out but still-running attempt cannot write concurrently with
its replacement. Multi-agent child runs must retain parent/child identity and
their own recovery state; a restored parent must not blindly spawn duplicate children.

The `interrupt_on` example needs a public completion path. Proposed additions are
run status, event subscription, cancellation, and an approval-response API.
Persist approval IDs and decisions so an application can reconnect and answer
without repeating the edit or losing the question. Preserve native permission
and tool-selection semantics during translation.

## Build sequence and definition of done

1. **Shared contract plus DeepAgents.** Implement profile loading, adapter
   registration, messages, run identity, direct execution, durable execution,
   real gateway calls, and tool/MCP use. Promote the existing crash proof into
   integration coverage.
2. **All six selectable through that contract.** Implement Pydantic AI, Claude,
   Codex, and the two pinned OpenCode variants. Ship a comparison example that
   takes six compatible profiles and runs the same task from isolated copies of
   the same workspace. Report outputs, diffs, tests, duration, and available usage;
   mark unavailable metrics rather than inventing them.
3. **Complete the README's behavior.** Finish live/reconnectable streaming,
   subagents and model overrides, per-operation retries, both fallback types,
   native middleware, and approval/resume. Publish the tested capability matrix.
4. **Release and self-hosting readiness.** Pin supported runtime ranges; document
   installation and migration; provide local and self-hosted examples; test shared
   storage, worker ownership, version changes, and bounded retention/history.

Milestone 2 is useful for the user's harness comparison, but it is not completion
of the full README. All four milestones belong to the target release scope.

For each adapter, run the same contract tests and real integration cases:

- Start, stream, tool result, final answer, follow-up in the same session.
- MCP tool and supported subagent model/tool overrides.
- Client disconnect/reconnect without restarting the task; duplicate start ID.
- Worker crash after a completed tool and during an in-flight tool.
- Harness process/server disconnect and loss of the checkpoint store.
- Retry limits, model fallback, and rejection of harness fallback after tool start.
- Pause for approval, reconnect, approve/deny, and explicit cancellation.
- Unsupported feature/model/protocol combinations fail before external work begins.

Use scripted unit/contract tests for repeatable edge cases and real upstream
harness/provider tests for acceptance. A stub that returns the right message
shape does not count as a working harness. Test side effects against disposable
fixtures and fault-inject failures around operation completion/checkpoint writes.

## Sources reviewed

- [Full LiteAgents v2 proposal](https://github.com/BerriAI/liteagents/tree/main/v2-proposal)
- [DeepAgents starter and verified recovery results](../cookbook/temporal/README.md)
- [Pydantic AI Temporal integration](https://ai.pydantic.dev/durable_execution/temporal/)
- [Claude Agent SDK Python](https://github.com/anthropics/claude-agent-sdk-python)
- [Official Codex SDK documentation](https://developers.openai.com/codex/sdk)
- [Codex app-server protocol](https://developers.openai.com/codex/app-server)
- [OpenCode server documentation](https://opencode.ai/docs/server/)
- [OpenCode SDK exports](https://github.com/anomalyco/opencode/blob/dev/packages/sdk/js/package.json)
