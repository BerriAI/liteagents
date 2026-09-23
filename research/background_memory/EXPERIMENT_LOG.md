# Historical experiment log

This is an append-only account of hypotheses, implementation changes, trials and
decisions. Corrections belong in new entries. Completed, failed and interrupted
trials remain in `results/traces.json.gz`; exact configurations and metrics are
in `results/summary.json`. The append-only `results/events.jsonl` records run
starts/outcomes, UTC recording times, source digests and cost reservations.

Entries through v5 below were reconstructed on 2026-09-23 UTC from retained
results, commits and research notes. Version order is known; exact trial start
times were not recorded and are not inferred from file modification times.
Commit times refer to commits, not to model request times. New runs record their
actual start/end timestamps. Credentials and private account data stay outside
all these artifacts.

## Reconstructed: design and upstream review

- Inspected Codex PR #42385 and source at the 0.153.4 release. Its experimental
  context budgets, history/notes and fresh-context tool provide the conceptual
  reference. The inspected code does not schedule a separate cheaper observer.
- Built on Yujong's Liteagents PR #5 at `8b7c191b5b085d476e541a9c1004c1ffdf7bcb81`:
  synchronous compaction, composable triggers/strategies, atomic history edits,
  structured token counting, usage anchors and replayable events.
- Experiment: bounded main input plus incrementally maintained notes and exact
  history recovery. Host owns source IDs/coverage; one observer can overlap the
  main call. The in-process archive grows; application persistence is out of scope.

## Reconstructed: v1–v3, seed 719

- v1: background release tasks passed, but short context cost $0.0616 versus
  $0.0417 for full history. Decision: defer observation and recovery tool schemas
  until useful, so tiny conversations do not pay the memory machinery overhead.
- v2: a long inventory trial failed because generated notes exceeded the local
  budget. Decision: validate notes and permit one repair from the same original
  evidence; provider output limits alone are insufficient.
- v3: incident processing stalled in repeated reads, updated only one of twelve
  records, and reached an 81-second median turn. The current request looked
  unstarted after its entire tool group left active context. Decision: retain
  protocol-valid completed-call receipts pointing to archived results.
- These runs used evolving code and mostly a bytes/3 counter. They are diagnostic
  history, not controlled estimates of the final design's performance.

## Reconstructed: v4, seed 901

- Added receipts and policy experiments (`436d27a`, 2026-09-23 04:18:31 UTC).
  An aggressive 640-token note trial exposed truncated observer output. Added
  one bounded same-source repair (`842b04f`, 04:21:16 UTC).
- Fixed manual multi-observation rollback and replayable aggregate updates;
  preserved completion status in observer evidence. Last SDK change before v5
  was `0862fdb`, 2026-09-23 04:43:06 UTC.
- Matched full/background long trials passed all five workflow families with
  8k main / 1k note / 4k batch settings. Combined background cost was about $3.22
  versus $4.00 for cached full history. This profile is explicit research
  configuration, not a silently changed SDK default.
- Synchronous summaries passed inventory but lost three original catalog fields
  on late lookup. Background recovery succeeded. JSON, terse and delta notes all
  passed these two cases; there was no universal winner across format choices.
- Cache placement mattered: moving notes before the current request raised
  release cost from $0.7485 to $1.4256 and reduced cache reads from 59% to 12%.
- Strict one-turn context passed and reduced peak input to 2,400 tokens, but
  median latency rose to 4.76 seconds. Eager incoming-request observation reduced
  this to 2.96 seconds in the matched exploratory run. It remains research-only.
- User reading time, larger batches and incoming-request overlap were measured;
  none justified promoting every harness lever into the SDK API.
- Luna-only full-history controls passed inventory ($0.0175) and lookup ($0.0141).
  These fixtures do not establish a need for Astra's reasoning ability.

## Reconstructed: v5 validation, seed 9273

- Ten fresh short trials: all five full-history trials and four background
  trials passed. Background inventory failed when Astra emitted an opaque
  `redacted_thinking` block unsupported by LiteLLM's local token counter.
- All five full-history long holdouts completed successfully. Background release
  halted at the hard context boundary after the deliberately injected observer
  outage. This is the documented fail-safe behavior, but that application had no
  recovery policy, so the task was incomplete and is retained as a failed trial.
- Researcher interrupted the owned sweep during background inventory to fix the
  tokenizer path. Its checkpoint and all ledger costs, including the cancelled
  request reservation, are preserved as `research-aborted`, not scored as model
  quality. Queued scale/source-review trials had not begun.

## 2026-09-23 UTC: v6 preparation

- Count known opaque blocks conservatively by serialized UTF-8 bytes in a
  detached counting copy. Preserve original provider messages for replay;
  unrelated tokenizer failures still raise. Regression covers real local token
  counting, nonmutation and multi-turn continuation with original opaque data.
- Added this log, automatic append-only run events and unique-version enforcement.
  Source digests are frozen once per process; edits between cases stop the run
  rather than relabel code already loaded in memory.
- Added an explicit research application recovery policy: on a context-budget
  halt, compact and continue the pending request once, without replaying the
  original action. Report recovery separately from uninterrupted success.
- Next: fresh short/long validation, scaled histories, real public-source review,
  and the aggressive tool-receipt regression. Preserve the $200 authorization as
  a ceiling, with the harness limited to $190; spend is not a target.
