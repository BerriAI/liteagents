# LiteAgents v2 development

The public API uses profiles and native harnesses. V1 compatibility lives in the
explicit legacy namespace. Keep the product README, SDK contract, migration
guidance, and runnable cookbooks aligned with the implementation. Test changes
continuously. Do not claim unsupported native recovery guarantees.

## Layout

- agent.py: v2 public client/options. profiles.py: validated configuration.
- types.py/runs.py: common events, results, and run controls.
- harnesses/: native adapters; each framework owns its agent loop.
- runtime/: direct execution, recovery, approvals, and tool boundaries.
- storage/: SQLite/PostgreSQL run state, operation records, events, ownership.
- temporal/: deterministic orchestration, clients, workers, and activities.
- legacy/: isolated v1 compatibility namespace, with regression tests.
- cookbook/: portable, independently runnable feature demonstrations.

Every source file stays below 500 nonblank lines (scripts/check_loc.py).
Optional dependencies must not break other adapters or the core package.

## Correctness

Fail explicitly for unsupported combinations. Distinguish native session resume
from operation recovery. Attaching never resubmits. Keep secrets/native objects
out of workflow arguments. Persist permission decisions and completed operations;
an interrupted external effect still requires application idempotency.

Test real native loops, MCP, worker loss, concurrent ownership, version mismatch,
approval/resume, cancellation, retention and bounded histories. Keep live model
tests opt-in and bounded. Validate PostgreSQL and self-hosted deployment examples.
Run lint, types, line limits, packaging checks and the relevant regression suite.
