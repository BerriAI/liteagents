# Background context research

This experiment replaces a growing active transcript with bounded working notes,
an unprocessed recent tail, and tools that recover exact original messages. A
second model prepares the notes while the main model works. It is an optional
`BackgroundMemoryOptions` policy in Liteagents' existing compaction slot.

The [historical experiment log](EXPERIMENT_LOG.md) records hypotheses, failures,
changes and decisions. [Run events](results/events.jsonl) are append-only and
include source digests, configurations, timestamps and outcomes. Early events
are explicitly reconstructed; interrupted trials retain all reserved costs and
are labeled separately from completed quality failures.

## Design and boundaries

1. Append every original typed message to a client-local archive. Message IDs are
   one-based positions in `client.transcript`; generated notes are not originals.
2. Start at most one observer on a complete, previously unprocessed prefix. Its
   input is previous notes plus new events, rather than the entire conversation.
3. Continue the main loop while there is room. Adopt finished notes only before
   the next model request, atomically replacing exactly the covered prefix.
4. Keep all unprocessed messages and the current human request verbatim. At the
   configured request or turn limit, wait for the observer. If observation fails
   or an indivisible group cannot fit, raise instead of silently dropping it.
5. Expose bounded history search/read tools after eviction. The main model can
   recover facts that the notes omitted, including details not known to be useful
   when they were first seen.

This bounds **model input**, not process RAM: the original archive still grows
for the lifetime of the client. Persistence/resume and cross-client memory are
outside this in-process SDK's scope. Token counts are estimates, not a guarantee
that a provider will use precisely the same tokenizer. A large current user
request or an oversized indivisible tool group can still require larger limits.

Cancellation preserves completed tool results and marks unresolved calls as
outcome unknown. The model must inspect state before retrying an action that may
already have happened. The observer has no tools and cannot execute actions.

## Relation to prior work

[Yujong's PR #5](https://github.com/BerriAI/liteagents/pull/5) provides synchronous
compaction: composable triggers, summary/pruning strategies, atomic updates,
token accounting, and replayable events. This experiment builds on those public
contracts and keeps ordinary compaction available.

[Codex PR #42385](https://github.com/openai/codex/pull/42385) activates experimental
token-budget context, history/notes, and `new_context` for eligible Codex backend
sessions. The [0.153.4 implementation](https://github.com/openai/codex/tree/rust-v0.153.4)
was inspected, including `compact_token_budget.rs`, the `new_context_window`
handler, and the history-notes extension. Its reset path starts a new context
without a summarization call; the agent can maintain notes and retrieve history.
The public code inspected does not implement this experiment's parallel,
cheaper-model observer. Here, the host owns the coverage cursor and scheduling,
and the observer supplies only note content.

## Reproduce the live comparisons

Install this checkout's development dependencies. Create a private directory
outside the repository, put the gateway key in `gateway.key` with restrictive
permissions, and pass only that directory's path. No credential belongs in a
command committed to this repository or in a results file.

```sh
PYTHONPATH=src python research/background_memory/run.py \
  --private-dir /absolute/private/directory \
  --output research/background_memory/results \
  --version example --modes full,summary,background \
  --scenario inventory_long --seed 1234 \
  --context 8000 --memory 1000 --min-observation 4000
```

The gateway is the user's LiteLLM sandbox; the harness uses its Anthropic-compatible
messages endpoint. Main model: `openai/gpt-6-astra`; observer:
`openai/gpt-5.6-luna`. Both use low reasoning effort. `--main-model luna` is a
separate difficulty/cost control, not a silent substitution for Astra.

Only one paid harness process should run at a time. Every attempt reserves a
conservative cost in a shared private `ledger.json` **before** it starts. The
ceiling is $190, leaving headroom below the authorized $200 maximum. Completed
calls use the gateway's response-cost header, with usage-based fallback; failed
or cancelled calls retain their reservation. The ledger must not be reset across
iterations. Results include call-level costs, real cache usage, model roles,
latency, final answers, and deterministic checks. They exclude request credentials.

Models receive no shell, browser, filesystem, network, or desktop tools. Workloads
use generated data and disposable in-memory state. No real user messages or
private repositories are sent to the research gateway.

## Levers

- `--context`, `--memory`, `--observation`: main input, working notes, and observer
  input budgets. The hard bound includes system and tool schemas.
- `--min-observation`: batch new content before refreshing notes; small histories
  can continue without any observer or recovery-tool overhead.
- `--turns`: optional raw human-turn cap; zero means use token limits alone.
- `--style working|json|terse|delta`: prose task state, structured state, terse
  key/value state, or incremental updates with periodic checkpoints.
- `--layout prefix|before_current_request` and `--stable-notes`: research-only
  wire-layout and metadata experiments for cache behavior.
- `--user-pause`: explicit time for the observer while the simulated user reads.
  This delay is excluded from turn latency and must be reported in comparisons.
- `--schedule after_response|eager_input`: whether observation can include the
  incoming request while the main model responds; publication still preserves it
  verbatim. This is a research scheduling experiment.
- `--scale`: multiply long-workload length. `--heldout` injects interruption and
  observer failure into the release workflow. Seeds change identifiers and facts.

Research policy variants patch the boundary only inside the harness process.
They are not extra SDK features or defaults. The final report distinguishes
exploratory settings, failed attempts, controls, and fresh-seed validation.

The `codex_review` scenario uses six real public source files from Codex 0.153.4,
with their license and provenance in `fixtures/`. It checks current gateway
eligibility, reset semantics, read-only history, search behavior and exact limits.
The code is passed as review evidence and is never executed.

Serial matrices are available through `sweep.py --phase explore|pipeline|holdout|scale`.
Use a fresh `--version-prefix` for repeat sweeps, a fresh `--seed` for holdouts,
and the same private ledger directory across
phases. The report explains why the exploratory versions are not all directly
comparable. Complete local JSON files can be packaged with:

```sh
python research/background_memory/analyze.py
```

Run one paid process at a time; the shared ledger lock is process-local. Do not
edit SDK or harness Python files during a run: code is imported once, digests are
frozen at process start, and edits between cases cause a stop. Reusing a result
label is rejected instead of overwriting an earlier trial. Checkpoints survive
interruption. `--recover-context` explicitly tests one application-level
`compact()` then continuation per blocked turn; recovery events and their costs
are reported and must not be described as uninterrupted success.

To read the committed complete traces without running any models:

```python
import gzip, json
from pathlib import Path
traces = json.loads(gzip.decompress(Path(
    "research/background_memory/results/traces.json.gz"
).read_bytes()))
```
