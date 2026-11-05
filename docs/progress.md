# BTC Sentinel progress

## Current position

Phase 11 historical-input validation at the Phase 12 activation gate.
Phases 1–11 have implemented, tested frameworks; the strategy has not passed
performance validation. Phase 13 forward paper observation has not started.

- PR #36: rules-v0.7 research and development-input validation; draft.
- PR #37: separate private scripted Telegram demo; draft, not strategy evidence.
- `main`, sealed 2022–2025 evidence and the untouched 2026 holdout are unchanged.
- No production dispatch, exchange writes or real trading are enabled.

## Completed checkpoints

1. Private demo: Telegram delivery fix and commands verified; D1 pause/resume
   persistence verified across redeployment. Additional-account live test was
   declined; automated authorization tests remain. No scheduled engine runs.
2. Original 2021 market-only readiness audit: 34,970 boundaries, zero complete
   six-timeframe analysis inputs, `performance_verdict=NOT_RUN`. Exact report
   preserved in `docs/evidence/rules-v0.7-development-readiness-2021.json`.
3. Frozen native weekly development method: official archives for January 2020
   through November 2021 only. No strategy rule or performance criterion change.
4. Native weekly experiment: strict import rejected. All 23 archives and their
   published checksums passed, but 7 of 43 complete overlapping weeks disagree
   in volume/trade fields. The first disagreement was independently reproduced
   directly from 10,080 raw minute rows. See
   `rules-v0.7-weekly-warmup-result.md` and the exact evidence under `docs/evidence/`.
5. Source triangulation: on 2021-01-11, all 1,440 daily-archive minute rows
   agree with current minute REST records; 10 monthly-archive rows disagree.
   Daily-minute sums also agree exactly with the current daily candle. This
   identifies a concrete monthly-input discrepancy, not a resampling bug.
   Method 2 was separately frozen in `rules-v0.7-daily-rest-method.md`.

## Current increment

Acquire all 365 daily-minute archives for 2021 uniformly, retain and repeat-check
the bounded official weekly REST response, then apply the same exact weekly
overlap gate and full-year readiness audit. Preserve the rejected archive method
as an expected-rejection regression, not an accepted method. No risk input,
signal, trade or performance is evaluated by this increment.

## Remaining gates

1. Resolve or independently justify the pre-2022 source-consistency method;
   freeze a separate method before another readiness experiment. Then confirm
   complete development analysis inputs exist and retain exact results.
2. Validate point-in-time official risk evidence through an approved acquisition
   path; SEC downloads from GitHub runners have returned HTTP 403.
3. Freeze development replay inputs/method and run research diagnostics.
4. Freeze the strategy and protocol before one untouched holdout evaluation.
   Failed or inconclusive evidence never authorizes deployment or tuning on
   consumed holdout data.
5. Reconcile the stacked demo branch before a future merge; preserve its native
   fetch fix and existing deployments, secrets and database.
6. Complete full Phase 12 preview/runtime acceptance under explicit activation
   review. Existing demo deployment is not full-engine activation.
7. Begin Phase 13 paper observation only after its gates are accepted.

Every checkpoint update should state: what changed, actual check/result,
current phase, blocker, and next remaining gate.
