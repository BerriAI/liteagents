# LiteAgents v2 development

The user authorized replacing the original SDK with the v2 proposal. The target
is milestones 1 and 2 in v2-proposal/IMPLEMENTATION_PLAN.md: a shared SDK, all six
native harnesses, DeepAgents Temporal recovery, and a reproducible comparison.
Test continuously and preserve existing regression coverage.

## Layout

- agent.py: public client/options and legacy compatibility.
- types.py: common messages. runs.py: results and handles.
- profiles.py: validated configuration and YAML/JSON loading.
- harnesses/: adapters and capability checks; native harnesses own their loops.
- runtime/: direct execution, tools, and message conversion.
- temporal/: durable client, worker, workflows, and activities.
- Existing loop.py, history.py, routers/, fusion.py retain legacy behavior.
- cookbook/: runnable applications and comparisons.

Every source file must stay under 500 non-blank lines (scripts/check_loc.py).
Split by responsibility. Optional imports must not break other adapters.

## Correctness

Reject unsupported options. Never equate session resume with proven operation
recovery. Attaching must not resubmit the prompt. Keep secrets/native objects
out of Temporal history. Scope environment and workspace state to each client.
Use native dependencies in integration tests; distinguish simulated providers
from live providers. Keep live tests opt-in and bounded. Run lint, type checks,
line-count checks, appropriate tests, and packaging checks.
