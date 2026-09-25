# Bounded context maintained by a background model

This is an empirical SDK experiment, not a claim that memory is universally
cheaper or more accurate than cached full history. The intended benefit is that
model input stops growing with the conversation, while exact original evidence
remains recoverable. The archive itself still grows in process memory.

The [historical log](EXPERIMENT_LOG.md) preserves the development sequence and
decisions, including failed versions and interrupted validation. Machine-readable
[run events](results/events.jsonl) are append-only; reconstructed early entries
are labeled rather than assigned invented trial timestamps.

## Method

Astra (`openai/gpt-6-astra`) handles the task; Luna (`openai/gpt-5.6-luna`)
maintains memory. A Luna-main control measures how demanding these tasks really
are. All calls use low reasoning effort through the authorized LiteLLM sandbox.
The model has only fixture tools; no computer, filesystem, shell, or network
control is available to it.

Five generated, multi-turn workload families have independent deterministic
checks: release planning with corrections and rejected proposals; cumulative
inventory changes with intermediate checks; incident read/update workflows with
large tool outputs; late lookup of arbitrary old catalog records; and transfers
interrupted after an action commits but before its result arrives. The transfer
workflow checks completed actions as well as final answers. Long variants add
irrelevant logs/catalogs. Fresh seeds change facts and identifiers. These are
controlled synthetic workflows, not results on LiteSpeedBench, SWE-bench, or a
representative sample of production conversations.

Comparisons include full history with **actual provider cache usage**, PR #5's
synchronous summarization, and background memory. No baseline is assumed to be
uncached. Costs include the main model, the observer, repair attempts and recovery
calls; ambiguous cancelled/failed calls keep a conservative reservation. Median
and p95 latency measure each user turn, excluding any explicitly configured
simulated user pause. A final already-running observation is settled for cost
accounting after the last answer, outside turn latency. All trials use the same
reasoning setting; low temperature or deterministic sampling is not assumed.

**Measurement correction:** v1–v6 allowed gateway whole-response caching. A v7
diagnostic reproduced identical response IDs and a 0.09-second repeated response,
even though the cache-hit header was absent. Those early cost/latency comparisons
are exploratory and potentially contaminated. Starting with v7, whole-response
reuse is explicitly disabled, while provider prompt-prefix caching remains
enabled. Response-ID hashes provide an additional audit trail. Costs are gateway
reported charges or usage-based estimates, plus reservations for ambiguous calls;
they are not independently reconciled account invoices.

The shared private ledger enforces a $190 ceiling under the authorized $200
maximum. Its credentials and raw account metadata are excluded from this PR.
`results/summary.json` contains compact metrics and exact checks;
`results/traces.json.gz` preserves complete synthetic results, including failed
versions, model usage, note content and configurations. Re-run `analyze.py` on
local result files to rebuild both artifacts.

## General mechanisms learned from failures

- **Small conversations need a threshold.** The initial version called the
  observer too early and added recovery schemas to tiny prompts. It cost $0.0616
  versus $0.0417 for the short release task. The implementation now avoids both
  until observation/eviction is useful.
- **Memory size needs validation and bounded repair.** A provider's output-token
  limit does not guarantee agreement with the configured counter. An early
  inventory run failed on oversized notes. The observer retries once from the
  same original events with a stricter size instruction. Truncated output gets
  the same repair path; an incomplete result is never committed.
- **Execution position is part of context.** Evicting an entire large tool group
  could make the current human request look unstarted, causing repeated reads.
  An early incident run updated only one record and had an 81-second median turn.
  The implementation now retains a complete tool-call/result receipt referencing
  the archived output. It says the call returned, without claiming its action
  succeeded. Existing error flags remain intact.
- **Recovery must search originals.** Search ignores copies inside earlier
  recovery tool calls/results, preventing recursive copies from crowding out
  source evidence. Exact reads still expose the full original transcript.
- **Boundaries need host bookkeeping.** The host assigns source IDs and coverage;
  the observer cannot claim it processed unseen messages. Finished observations
  replace only their covered prefix, even if newer messages arrived meanwhile.
  Already-covered idle prompts can leave when a new request arrives, without
  another observer call. Manual multi-step updates remain replayable.
- **Cancellation is an uncertain outcome.** Completed tool results are retained.
  Outstanding calls become explicit outcome-unknown results, and continuation
  must inspect state before retrying. The live transfer fixture deliberately
  cancels after committing an action to exercise this distinction.

## Corrected v7 validation (fresh seed 29111)

Whole-response reuse is disabled. Settings are 8k main input / 1k notes / 4k
observation batches, with ordinary working notes and after-response scheduling.
All ten short trials passed with zero observer calls: full-history cost $0.2990,
background-policy cost $0.2956. That small difference reflects stochastic outputs,
not a demonstrated short-context savings advantage. All 88 short-trial response
identifiers were distinct.

| Workflow | Full / memory cost | Full / memory peak input | Full / memory median seconds | Both pass |
|---|---:|---:|---:|---:|
| release | $0.970 / $0.763 | 39,092 / 7,720 | 1.99 / 2.11 | True |
| inventory | $0.852 / $0.820 | 32,889 / 7,432 | 2.08 / 2.25 | True |
| incidents | $1.130 / $0.718 | 34,560 / 6,922 | 8.21 / 8.23 | True |
| lookup | $0.707 / $0.628 | 29,871 / 7,252 | 2.13 / 2.32 | True |
| workflow | $0.383 / $0.390 | 13,687 / 6,298 | 4.44 / 4.78 | True |

Combined long cost was **$3.3183 versus $4.0418 (17.9% lower)**.
Memory's largest measured input was 7,720 tokens. These are single-seed paired
workflows, not confidence intervals. Inventory saved only about 4%, while the
interrupted-transfer workflow cost slightly more with memory. The full inventory
baseline cached 92.6% of its input; the observer itself cost only $0.0121. Cache
invalidation at the main model can dominate the price of the cheaper observer.

The release pair includes a user interruption and one injected observer outage.
The background run halted at its bound and recovered through one explicit
application `compact()` then continuation at turn index 5. Its recovery cost and
latency are included; it is not an uninterrupted-success claim. Incident and
transfer checks verify actual fixture state and exactly-once actions, in addition
to the final answer. Late lookup verifies original facts alongside a later
correction to current state.

### Three-times-longer histories (fresh seed 4013)

The same 8k / 1k / 4k profile is used without a turn cap or artificial user pause.

| Workflow | Turns | Full / memory cost | Full / memory peak input | Full / memory median seconds | Both pass |
|---|---:|---:|---:|---:|---:|
| release | 68 | $5.435 / $2.391 | 116,655 / 7,829 | 2.99 / 2.33 | True |
| lookup | 57 | $3.700 / $1.933 | 89,091 / 7,768 | 2.73 / 2.52 | True |

Both tasks passed their exact checks. Combined cost was $4.3235 versus
$9.1349, a 52.7% reduction. The larger release case reached 116,655
tokens with full history while memory remained below 8,000. This supports a
bounded-input benefit at these horizons; it does not prove perfect recall for
arbitrary tasks or an indefinitely growing archive.

### Review of actual Codex 0.153.4 source

Nineteen turns cover six public Rust files, followed by seven exact source checks.
The source is review data, never executed.

| Policy | Correct fields | Cost | Peak input | Median seconds | Recovery calls |
|---|---:|---:|---:|---:|---:|
| full | 7/7 | $0.509 | 13,853 | 6.95 | 0 |
| summary | 6/7 | $0.549 | 7,695 | 7.71 | 0 |
| background | 7/7 | $0.618 | 7,528 | 7.94 | 5 |

The synchronous-summary answer returned `null` for the note-file size limit;
its other six fields were correct. Background memory answered all seven fields
and made five recovery calls. Full history was cheaper and faster than memory on
this task: reducing a roughly 14k-token context was not a cost win. Source-review
accuracy and cost are reported together; this is not a broad coding benchmark.

### Strict one-turn and aggressive-budget checks

The following paired release trials share seed 29111, a one-human-turn cap,
and a one-token observation threshold. Unlike the long holdout above, neither
injects an observer outage or user interruption; compare these two schedules
directly, not their cost against that different fault-injection setup.

| One-turn schedule | Pass | Cost | Peak input | Median seconds |
|---|---:|---:|---:|---:|
| After response | True | $0.691 | 2,385 | 4.90 |
| Incoming request | True | $0.701 | 2,476 | 3.32 |

Observing the incoming request hid part of the wait, but did not eliminate it.
The eager scheduler is a harness-only experiment, not a new public SDK option.
A separate incident regression with 4k main input / 640-token notes passed all
12 exactly-once writes at a 3,991-token measured peak ($0.806; 8.35s median).
This verifies the general completed-call receipt and bounded note-repair fixes.

### Fresh Luna-only controls

- `codex_review_41344`: passed at $0.0115, 14,235 peak tokens.
- `lookup_long_29111`: passed at $0.0142, 29,872 peak tokens.

Both use full history and disabled gateway response reuse. The same source-review
and lookup tasks are solved much more cheaply by Luna alone. These experiments
validate context handling; they do **not** establish that Astra is necessary for
the task's reasoning. Model choice can dominate the observer optimization.

## Exploratory comparison (seed 901; response caching not controlled)

Working notes, 8k input budget, 1k note budget, and a 4k observation batch.

| Workflow | Full / background cost | Full / background peak input | Full / background median seconds | Both pass |
|---|---:|---:|---:|---:|
| release | $0.965 / $0.746 | 39,036 / 7,866 | 2.18 / 2.12 | True |
| inventory | $0.852 / $0.775 | 32,901 / 7,352 | 2.09 / 2.07 | True |
| incidents | $1.092 / $0.697 | 34,478 / 7,420 | 7.50 / 7.53 | True |
| lookup | $0.707 / $0.627 | 29,872 / 7,267 | 1.92 / 2.53 | True |
| workflow | $0.381 / $0.370 | 13,643 / 5,579 | 5.19 / 4.86 | True |

These are exploratory observations, not confidence intervals or the final
performance comparison. Fresh-seed validation uses the corrected v7 harness.

### Note format and synchronous control

| Policy | Inventory cost / pass | Late lookup cost / pass |
|---|---:|---:|
| Full history | $0.852 / True | $0.707 / True |
| Synchronous summary | $0.530 / True | $0.457 / False |
| Working notes | $0.775 / True | $0.627 / True |
| JSON | $0.792 / True | $0.572 / True |
| Terse | $0.797 / True | $0.618 / True |
| Incremental delta | $0.747 / True | $0.621 / True |

Synchronous summarization returned `null` for the three old catalog fields it
could no longer recover. Every background format recovered all four exact fields.
Luna-only full-history controls also passed these two tasks at $0.0175 and $0.0141;
these workloads do not establish that Astra is required for their reasoning.

### Scheduling and cache layout

| Release policy | Cost | Peak input | Median seconds |
|---|---:|---:|---:|
| Batched working notes | $0.746 | 7,866 | 2.12 |
| Delta with stable banner | $0.748 | 7,567 | 2.18 |
| Notes before current request | $1.426 | 7,595 | 1.94 |
| Strict one human turn | $0.697 | 2,400 | 4.76 |
| Two-second user pause | $0.724 | 7,757 | 1.96 |

All five passed. Moving notes near the current request reduced cache reads from
59% to 12% and nearly doubled cost versus the matching stable-prefix variant.
The strict turn limit reduced active input further but added waiting. The user
pause is excluded from reported latency and adds two seconds between each turn.
For incidents, a 16k cap / 8k observation batch passed at $0.729 and a 12,486-token
peak, versus $0.697 and 7,420 tokens with the 8k / 4k policy.

## Interpretation

For a growing transcript, cached full history still reads more tokens on every
turn. A bounded prompt plus incremental observation has approximately constant
input per step, excluding additional recovery. At shorter horizons, however,
cache reads can be much cheaper than repeatedly prefilling refreshed notes.
The full-history long controls here typically cache more than 90% of input.
Batching and cache placement therefore matter alongside note size.

A strict one-turn limit cannot promise zero waiting: if the observer has not
finished, the host must either wait, grow the prompt, or discard unseen evidence.
This implementation waits at the configured limit. Below that limit, main and
observer calls overlap. The raw tail is a latency allowance, not a fixed
three-turn design requirement.

An original archive prevents irreversible deletion but does not guarantee that
the model will notice a missing fact or retrieve the right evidence. Exact
lookup, cumulative updates, action checks, interruptions, and failures must all
be measured; a small prompt and a low bill alone are insufficient success criteria.

## Accounting and final verification

The [aggregate ledger](results/accounting.json) accounts for **$65.1987**
across 2,833 authorized gateway attempts: $61.7581 in completed
reported/estimated usage and $3.4406 reserved for three cancelled calls.
There are no pending requests. Astra accounts for $64.3675; Luna for
$0.8312. Four preliminary diagnostic requests outside result artifacts
are included in the ledger total. The $190 harness ceiling remained below the
user's $200 authorization; the budget was a ceiling, not a spending target.

All 941 completed responses in the retained v7 result artifacts with gateway reuse
disabled have distinct response ID hashes. Every v7 result's source/harness
digest matches frozen revision `1c346a6`;
subsequent changes package results and add aggregate accounting. The SDK checks
pass 295 tests, Ruff, mypy and the 500-nonblank-line gate, with all six CI
Python/MCP combinations passing before final results packaging.

The historical artifacts retain 104 scored/aborted/diagnostic run records,
including seven failed trials and two researcher-aborted trials. Failed baselines
and obsolete implementations are not removed. These are complete stored result
records (answers, checks, usage, final notes and configurations), not a capture of
every wire request or every intermediate note revision.

## Scope and reproducibility

Exploratory v1–v3 runs used the earlier PR #5 API and mostly a conservative
bytes/3 counter. Code evolved between those runs; they are diagnostic history,
not a controlled final comparison. Early v4 also improved truncated-output repair
and manual rollback. v5 exposed the opaque-block counter problem; v6 fixed it
and exposed whole-response reuse at the gateway. v7 freezes source digests at
process start and disables response reuse. Earlier per-case digests describe
files on disk and should not be treated as proof of the code loaded in a process
while development was continuing. The latest implementation is based on
PR #5 commit `8b7c191b5b085d476e541a9c1004c1ffdf7bcb81`, with structured token
counts and request-usage anchors. Results record source digests and complete
parameters. Token caps refer to SDK estimates; small discrepancies from gateway
counts are expected and reported by peak actual input.

The harness also explores JSON, terse and incremental-delta notes; observation
batch sizes; turn limits; note placement; stable metadata; user reading time;
and longer horizons. These remain research policies, not a collection of SDK
flags. Current public API behavior is documented in the main README. Do not
infer production reliability, optimal default settings, or security guarantees
from one seed per cell in a synthetic matrix.
