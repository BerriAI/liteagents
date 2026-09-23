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

## 2026-09-23 UTC: v6 measurement correction and v7 cache control

- All ten v6 short trials passed, with no observer calls. However, two background
  cases had median latencies near 0.1 seconds. Their response-cache header was
  absent, so its absence could not establish that requests were fresh.
- Stopped the owned v6 long sweep to investigate; the interrupted full-release
  case is automatically retained as `research-aborted`, with its reservation.
- A four-request Luna diagnostic confirmed whole-response reuse: two identical
  requests with caching allowed returned the same response-ID hash, and the
  second completed in 0.09 seconds. With gateway `no-cache` / `no-store` controls,
  subsequent requests returned distinct hashes at 0.79 and 0.89 seconds.
- Decision: all regular research calls now disable gateway whole-response reuse
  and record response-ID hashes. Provider prompt-prefix caching stays enabled.
  Earlier v1–v6 latency/cost comparisons remain exploratory and potentially
  contaminated; they are not the final performance evidence. Earlier costs are
  reported/estimated usage charges and conservative reservations, not a verified
  account invoice. Source, outcomes and failed attempts remain unchanged.
- v7 reruns short/long matched controls, scaled histories, real source review,
  and the aggressive receipt case with this corrected measurement boundary.

## 2026-09-23 UTC: first v7 checkpoint

- Fresh seed 29111: all five short full-history cases and all five short
  background-policy cases passed. Both groups made zero observer calls. Total
  costs were $0.299047 and $0.2955775 respectively; this small stochastic
  difference is not evidence of a short-context savings advantage.
- All 88 response-ID hashes in those trials were distinct. CI passed all six
  Python/MCP combinations at `1c346a6`, including the response-cache controls and
  a test that rejects a paid request before sending when its reservation exceeds
  the shared budget.

## 2026-09-23 UTC: v7 observer outage recovery

- Fresh long release, seed 29111, passed all six exact fields after one injected
  observer failure. The host halted at the bound; the harness explicitly called
  `compact()` and continued the pending request once at turn index 5.
- Full/background peak input: 39,092 / 7,720 tokens. Cost: $0.9698395 /
  $0.76282585. Median turn latency: 1.99 / 2.11 seconds. The recovery's latency
  and calls are included; this is not an uninterrupted-success claim.
- Correction from the retained JSON: the full-history cost above is
  **$0.9698075**. The previously typed $0.9698395 was a transcription error;
  the background cost and rounded comparison are unchanged.

## 2026-09-23 UTC: v7 long holdouts complete

- All five full-history and all five background-policy long holdouts passed on
  fresh seed 29111. Combined cost: $4.0418490 / $3.3182627; reduction 17.9%.
- Peak background input was 7,720 tokens. Inventory savings were modest, and
  interrupted-transfer memory cost slightly more. Bounded input is the consistent
  property; cost savings remain workload dependent.
- Exact late lookup, all twelve incident writes, correction handling, and transfer
  receipts/balances passed. The explicit release recovery remains separately
  recorded. Scaled histories and public-source review are still in progress.

## 2026-09-23 UTC: v7 scale results

- Release (68 turns) and late lookup (57 turns) both passed in full and background
  modes with fresh seed 4013 and response reuse disabled. The memory profile was
  unchanged from the smaller holdouts.
- Combined full/background cost: $9.1348720 / $4.3234853 (52.7% lower).
  Full peaks were 116,655 and 89,091 tokens; background peaks were 7,829 and 7,768.
- Decision: retain batched working notes with exact retrieval as the measured
  starting profile; keep strict one-turn scheduling as a separate latency tradeoff.

## 2026-09-23 UTC: v7 public-source review

- Full history and background memory passed all seven Codex source checks.
  Synchronous summarization answered six and returned null for the note-file
  size limit. Its failure is retained, with the exact expected/actual fields.
- Background used five history recovery calls and cost about $0.618 versus
  $0.509 for full history. This roughly 14k-token workload did not justify
  memory on price. The report preserves this counterexample to universal savings.

## 2026-09-23 UTC: aggressive receipt regression

- Fresh incident seed 9273 passed with a 4,000-token main budget and 640-token
  notes. Measured peak was 3,991; all twelve incident
  writes occurred exactly once. Cost $0.8059708, median
  turn latency 8.35 seconds.
- This closes the earlier repeated-read / truncated-note regression without
  specializing the implementation to incident identifiers or expected answers.

## 2026-09-23 UTC: fresh strict-turn scheduling replication

- Both strict one-turn release trials passed with response reuse disabled.
  After-response observation: $0.691, 2,385-token peak, 4.90s median. Incoming
  request observation: $0.701, 2,476-token peak, 3.32s median.
- Earlier observation hid part of the latency at slightly greater cost. Keep
  eager scheduling research-only until broader action/tool workloads justify
  a public policy choice. A strict cap does not promise zero waiting.
- Fresh Luna-only full-history lookup also passed at about $0.0142. Do not infer
  that Astra is necessary for these tasks from the observer architecture.

## 2026-09-23 UTC: paid research complete and evidence audit

- Fresh Luna-only Codex review also passed. The final two Luna controls cost
  $0.0115 and $0.0142; both used full history. The report
  explicitly avoids claiming these tasks require Astra.
- Final ledger: 2,833 attempts, $65.19865942 charged/reserved,
  including $3.4405932 in conservative cancelled-call reservations.
  No requests remain pending; no more paid runs are queued.
- Every v7 source/harness digest matches `1c346a6`, and all 941 completed
  responses with reuse disabled have unique hashes. Aggregate accounting adds
  all four preliminary probes not represented in individual result artifacts.
- Keep the PR as an unmerged experiment. The measured 8k/1k/4k starting profile
  bounds input; strict one-turn scheduling, memory formats and cache placement
  remain explicit tradeoffs, with no universal quality or price guarantee.
- Audit scope clarification: the 941-response hash check covers retained v7
  result artifacts; preliminary probes are included separately in aggregate
  ledger accounting. No preliminary requests are treated as free budget.
