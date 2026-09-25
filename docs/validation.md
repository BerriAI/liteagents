# Release validation — September 25, 2026

## 0.2.0

The release packages the implementation tested at SDK revision
`3da45e67ccd550d6b4962e474c6d4029831e9574`; subsequent changes update documentation
and package metadata. Validation used Python 3.12.14 and the runtime versions
in `constraints-tested.txt`.

- **Release packaging passed:** wheel and source distribution report 0.2.0,
  strict metadata validation passes, and the wheel matches every SDK source
  file. The final packaging/documentation change also passed 140 local
  non-integration regressions (five dependency-specific skips), lint, types,
  and source-size limits.
- **Full Linux regression passed:** 244 tests, with 24 expected skips (23 opt-in
  live-provider cases and one PostgreSQL-only assertion under SQLite). All seven
  CI jobs passed across Python 3.11–3.13, MCP 1.12/2.2, native runtimes, Temporal,
  PostgreSQL, and the deployment image build.
- **67 focused checks passed:** 42 real-harness cases for application tools,
  shared MCP, selection defaults/empty/explicit lists, stable tool names, direct
  execution and Temporal reconnects; 25 configuration/capability checks, including preservation of usage reported
  at native turn completion.
- **23 live-provider checks passed:** file tools, streaming and conversation
  follow-up, shared MCP, durable MCP/reconnects across all six harnesses, and
  reopening native sessions. Native Claude/Codex turn usage is retained.
- **Fresh non-editable installation passed:** DeepAgents-only extra, with no
  Temporal SDK or PostgreSQL driver installed; simple agent, file/conversation,
  and application-tool recipes. Adding only the MCP extra enabled its recipe.
- **Cookbook checks passed:** simple-agent and application-tool recipes on all
  six harnesses; DeepAgents retries and model fallback. A live-model run of the
  durable recipe survived a killed worker and restart with exactly one receipt.
- **PostgreSQL checks passed:** seven shared-worker/storage checks, including
  approval reconnect and lost ownership. One SQLite parameter was skipped
  because that assertion specifically requires PostgreSQL.
- Lint, type checks, the source-size limit, wheel/sdist builds and wheel contents
  passed. [PR #8 checks](https://github.com/BerriAI/liteagents/pull/8/checks) run
  the complete regression suite and Linux native/Temporal/PostgreSQL matrix.

The application-tool/MCP matrix uses one profile shape, changing the harness and
Temporal configuration. Executable application tools are registered on the
client for direct execution and on the worker for Temporal. Deterministic
provider responses exercise actual native loops; the live checks separately
exercise compatible real gateway model aliases. This does not establish that
every native model setting or tool default is interchangeable.

## Earlier 0.2.0a1 release verification

The release branch replaces the v1 public API with the profile-driven SDK.
Validation uses actual native harness runtimes. Deterministic provider fixtures
make failure injection reproducible; paid gateway checks separately verify real
provider behavior. The two OpenCode names exercise the same pinned server.

The full unpaid suite passed **175 tests**, with **24 expected skips** (23 paid
provider cases and the PostgreSQL-only ownership case under SQLite). The final
retention/status/duplicate-submission changes then passed **6 focused Temporal
checks**, including termination without a worker and expired workflow history.

## Recovery and feature acceptance

| Check | Verified behavior |
| --- | --- |
| Worker process killed, all six harnesses | Reuses completed model/tool operations; retries the interrupted tool; finishes the original run |
| Worker killed while awaiting approval, all six | Reattached client sees the same approval ID; a persisted decision survives restart; the tool runs once |
| Worker killed inside a child, all six | Child operation scope and completed work survive; parent finishes without repeating the completed child lookup |
| Native retry and model fallback | Real Claude, Codex, and both OpenCode adapters exhaust two primary attempts per boundary, switch models, and execute each successful tool once |
| Tool restrictions | Native gateway rejects unmanaged returned calls; DeepAgents cannot dispatch a hidden built-in; children receive only their allowed tools |
| Cancellation | No tool dispatch when cancelled before the worker starts; pending native approvals cancel without invoking the gated tool |
| PostgreSQL | Shared workers, exclusive run ownership, loss of the ownership connection, atomic approval decisions and bounded events |
| Retention | Deletes terminal SDK state and owned graph/native checkpoints; preserves workspace artifacts |
| Versioning and replay | Wrong profile versions and changed operation inputs fail; exhausted operation budgets cannot reset on a new worker |
| Temporal history | Deterministic workflow replay passes; worker configuration stays out of history; the final result is an SDK-store reference |

`tests/test_native_recovery.py` launches the real CLI processes against the local
provider fixture. `tests/test_temporal_sdk.py` launches real workers and kills
them. These are process-crash checks, not just synthetic adapter return values.
The interrupted tool may begin twice but completes once in these fixtures.
That demonstrates the need for application idempotency around external effects.

## Live providers and cookbooks

The final focused live run passed **6/6** durable MCP/reconnect/streaming cases.
Each uses a real local MCP server, a real upstream model, a separate Temporal
worker, client detachment, and retrieval through a new client. Earlier live checks
also covered ordinary tools, conversation follow-up, native session reopen, and
the six-harness coding comparison.

All seven guided recipes passed with DeepAgents and a real model. Additional
recipe checks passed for Claude subagents, OpenCode v2 tool retries, and Codex
model fallback. The comparison recipe previously had all six harnesses repair
the isolated calculator fixture and pass its three unchanged tests.

The self-hosted Compose example was started in an isolated Docker environment.
Its worker image built successfully, a real model read `/workspace/hello.txt`,
and a separate client retrieved `self-hosted-verified` through Temporal and
PostgreSQL. The example's database creation and readiness checks were corrected
based on this actual startup test.

The acceptance gateway served `bedrock_mantle/openai.gpt-5.6-luna` for
Chat/Responses and `claude-code-sonnet-4-6-converse` for Anthropic Messages.
These are deployment-specific aliases; use compatible aliases on your gateway.

## Supported and tested versions

Python 3.11+ is supported; the full local environment uses Python 3.12.14. A
clean Python 3.13.15 wheel environment with MCP 1.12 and without harness/Temporal
extras passed **83** non-integration checks; **37** dependency-specific cases
were skipped and **79** integration/live cases were deselected.

| Component | Acceptance version |
| --- | --- |
| DeepAgents | 0.7.19 |
| Pydantic AI slim | 2.50.0 |
| Claude Agent SDK | 0.2.159 |
| OpenAI Codex SDK/runtime | 0.157.0 |
| OpenCode CLI | 1.18.29 |
| Temporal Python SDK | 1.33.0 |
| MCP | 2.2.0; separate core coverage at 1.12.0 |
| LangGraph SQLite / PostgreSQL checkpoints | 3.1.1 / 3.1.2 |
| psycopg / pool | 3.3.6 / 3.3.3 |
| Self-hosted Temporal / PostgreSQL / UI | 1.29.7 / 16 / 2.54.1 |

[constraints-tested.txt](../constraints-tested.txt) pins the native dependency
set. Package extras permit compatible ranges. Native CLI recovery depends on
stable protocol and prompt behavior, so qualify upgrades with the crash tests.

## Reproduce

```sh
pip install -e '.[all,postgres,dev]' -c constraints-tested.txt
npm install -g opencode-ai@1.18.29
# Start Temporal on localhost:7233 and use a dedicated test PostgreSQL database.
export LITEAGENTS_TEST_POSTGRES_URL='postgresql://user:password@localhost/test_database'
pytest -q
ruff check src tests cookbook/recipes
mypy src/liteagents --ignore-missing-imports
python scripts/check_loc.py
python -m build
```

Paid checks require `LITEAGENTS_LIVE=1`, `LITEAGENTS_API_BASE`, `LITELLM_API_KEY`,
`LITEAGENTS_MODEL`, and `LITEAGENTS_CLAUDE_MODEL`:

```sh
pytest -q tests/test_live_harnesses.py -k durable_mcp
```

CI covers Python 3.11–3.13 and MCP 1.12/2.2 for the core package. Its native job
starts the supplied PostgreSQL/Temporal Compose deployment, runs the full unpaid
suite, and builds the worker image. Live-provider checks remain opt-in and require
credentials. Lint, types, the 500-nonblank-line source limit, wheel/sdist contents,
and diff whitespace are also checked locally.

This is an early SDK release and a single-host self-hosting example. It does not
establish production capacity, multi-region failover, or exactly-once external
effects. Interrupted remote actions need application idempotency; worker moves
need preserved databases, runtime versions, and workspace files. Deployment
security, backups, and Temporal service topology remain operator configuration.
