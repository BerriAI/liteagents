# Bounded context maintained by a background model

This is an empirical SDK experiment, not a claim that memory is universally
cheaper or more accurate than cached full history. The intended benefit is that
model input stops growing with the conversation, while exact original evidence
remains recoverable. The archive itself still grows in process memory.

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

## Scope and reproducibility

Exploratory v1–v3 runs used the earlier PR #5 API and mostly a conservative
bytes/3 counter. Code evolved between those runs; they are diagnostic history,
not a controlled final comparison. Early v4 also improved truncated-output repair
and manual rollback; v5 validation uses frozen source. The latest implementation is based on
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
