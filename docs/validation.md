# Milestone 2 validation — September 25, 2026

Verified locally on `codex/temporal-getting-started`, using the native dependency
versions listed in [SDK details](sdk.md).

| Check | Result |
| --- | --- |
| Full regression/integration suite | 105 passed; 17 opt-in provider tests skipped |
| Built wheel, clean environment without optional harness packages, MCP 1.12 | 81 passed; 41 dependency/provider checks skipped |
| Lint and type checking | Passed |
| Source file size limit | All modules below 500 nonblank lines |
| Wheel and source distribution build | Passed; generated databases/state excluded |
| Native coding comparison | All six fixed the bug and passed three unchanged tests |

Live-provider checks were run in focused groups throughout implementation:
all six harnesses performed a file tool call, streamed text, and continued their
conversation; all six invoked a real local MCP server; Claude, Codex, and both
OpenCode names reopened native sessions; DeepAgents completed a real model/tool
run through the public Temporal SDK after client detachment. These cases are in
`tests/test_live_harnesses.py` for opt-in reproduction.

The comparison gave each harness an independent copy of the broken calculator.
Every diff changed only `return left + right` to `return left - right`; the tests
and source fixture remained unchanged. Timings varied from about 9 to 20 seconds
and include startup, with protocol-compatible models selected for each harness.
This establishes working native execution, not comparative model quality.

Automated fault checks include duplicate run IDs, failed/cancelled local handles,
concurrent query rejection, model-call limits, malformed protocol responses,
missing terminal events, cleanup after errors, actual MCP stdio/HTTP/SSE sessions,
and independent native OpenCode server ownership. A flaky OpenCode global SQLite
startup collision was reproduced, fixed with dedicated state directories and
ownership locks, and checked through eight consecutive concurrent-startup,
session-resume, and cleanup repetitions.

The public Temporal integration test kills a worker during a tool and starts a
replacement. The tool completed before the crash executes once; the interrupted
tool begins twice and completes once. Other tests cover detach/attach, duplicate
submission, exclusive checkpoint ownership, worker configuration excluded from
workflow history, and deterministic replay. These are single-host recovery tests.

CI now runs core compatibility across Python 3.11–3.13 and MCP 1.12/2.2, plus a
Python 3.12 native-library/Temporal job. The workflow configuration was updated
locally; hosted CI has not been run for this unpushed branch. Real provider tests
remain opt-in and require credentials and a running local Temporal service.

Milestone 3 remains: portable subagents, per-operation retries and fallbacks,
approval/resume, and reconnectable durable streaming. Production distributed
checkpoint/workspace storage and deployment are also outside this milestone.
