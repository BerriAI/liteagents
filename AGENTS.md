# AGENTS.md

Read this before touching `src/liteagents/`. It explains how the package is
organized and where new code goes.

## The five-question layout

The package is organized around five questions. Each question has exactly
one home.

| Question | Home |
|---|---|
| What's a message or content block? | `types.py` |
| What's an agent, and how do you talk to it? | `agent.py` (`LiteAgentOptions`, `LiteAgentClient`, `query()`) |
| How does one turn actually run? | `loop.py` (the tool-calling loop) + `history.py` (message bookkeeping) + `tools.py` (the `Tool` interface) |
| Which model handles this turn? | `routers/` (`ModelRouter` protocol, `StaticRouter`, `JevModelRouter` — every routing strategy lives here) |
| How does an agent delegate a subtask to another agent? | `fusion.py` |

### fusion.py is not a router

`fusion.py` involves model selection but does not belong in `routers/`. A
router decides *which model handles this turn* before the turn runs. Fusion
gives the main model a *tool* it can choose to call mid-turn; calling that
tool spins up a nested agent loop on a different (sidekick) model. Router
decisions happen before the loop; fusion decisions happen inside it. Keep
these mechanisms in separate files.

## The 500-line rule

Every file in `src/liteagents/` must stay under 500 non-blank lines. This is
enforced in CI by `scripts/check_loc.py` (wired into
`.github/workflows/ci.yml`). It is a hard gate — a PR that exceeds the limit
fails CI, no exceptions.

If a file is approaching the limit, split it by responsibility instead of
letting it absorb more concerns. Example: if `loop.py` grows past budget
because of tool-dispatch logic, extract a `dispatch.py` rather than keep
adding to `loop.py`.

## The Rust extension (`_native/`)

`_native/` is scoped only to synchronous, CPU-bound helpers with no I/O:

- the history buffer
- response content-block parsing/validation
- tool input-schema validation

It must never contain anything that awaits network I/O or runs user tool
code. The async tool-calling loop stays pure Python — bridging async Rust
into asyncio for I/O-bound work has real cost and no benefit here.

`_native/__init__.py` must fall back to a pure-Python implementation when the
compiled extension isn't available. The package always installs and works
without a Rust toolchain; `_native/` is a pure accelerator, never a
dependency.

## Where new code goes

- New routing strategy -> new file in `routers/`.
- New built-in tool -> `tools.py`, or its own small file if it's substantial.
- New example/demo agent (like `PRRiskAgent`) -> its own flat file at
  `src/liteagents/` (e.g. `pr_risk_agent.py`). Not a nested `contrib/`-style
  subpackage — that nesting was deliberately removed and shouldn't come back
  for a single-file example.
- CLI scripts that use the SDK but aren't part of the installable package
  (e.g. `cookbook/agent_sdk/pr_risk_agent.py`) go under `cookbook/`, never
  under `src/liteagents/`.

## What not to add

This is an in-process library that calls litellm directly, not a harness.
Do not add:

- permission systems or hooks
- sandboxing
- MCP server support
- session persistence/resume
- subprocess or CLI transport
