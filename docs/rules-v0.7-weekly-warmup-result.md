# Native weekly development experiment: rejected

## Phase and decision

Phase 11 historical-input validation at the Phase 12 activation gate.
The frozen strict-overlap method is **REJECTED**. Full Phase 12 activation and
Phase 13 paper observation remain blocked. No performance result was computed.

This is not a failed trading strategy test: the input-integrity gate stopped
the weekly supplement before analysis readiness, risk or signals were evaluated.
The original monthly-only readiness audit still reports zero ready boundaries.

## Immutable record

- Method freeze: commit `906d1f64a311ca9048b998ee944c3b7e25e0b48b`.
- First experiment: commit `0000f64d8dfe5c522565fd1ea9bbed3e63f64a85`.
- GitHub run: `41937674110`; ordinary CI passed, readiness workflow failed.
- Artifact ID: `13374124184`.
- Artifact ZIP SHA-256:
  `e00713f6c513e5f32e4ab76833ea7662ac1e7ffa597e0761073a79b55ab4c4cf`.
- Market manifest SHA-256:
  `5553d8ed38b19fd6700cba9df2dadd4c9d25af8eeae681264c147401f7359b16`.
- Native-weekly manifest SHA-256:
  `e3e464844f60df885302ed82788da6fff6757c22a8254b48f566cac1815bf1e5`.

All 23 weekly archives and publisher checksums passed structural validation.
Comparison of 43 complete overlapping weeks found 36 exact matches and 7
conflicts. The conflicts concern volume, quote volume, trade count and taker
volumes. Open/high/low/close prices and timestamps agree for all 43 overlaps.
The importer aborts and rolls back at the first conflict. It does not import
the weekly supplement or change any one-minute data.

| Conflicting week open (UTC) | Fields that disagree |
|---|---|
| 2021-01-18 | Volume, quote volume, trades, taker base/quote volume |
| 2021-02-01 | Volume, quote volume, trades, taker base/quote volume |
| 2021-03-29 | Volume, quote volume, trades, taker base/quote volume |
| 2021-08-16 | Volume, quote volume, trades, taker base/quote volume |
| 2021-09-20 | Volume, quote volume, trades, taker base/quote volume |
| 2021-10-18 | Volume, quote volume, trades, taker base/quote volume |
| 2021-10-25 | Volume, quote volume, trades, taker base/quote volume |

The full field-by-field machine-readable comparison is retained in
`docs/evidence/rules-v0.7-weekly-overlap-2021.json`. It is reproducible without
weekly import or signal evaluation:

```bash
python -m btc_sentinel.backtesting.weekly_overlap_job \
  path/to/market-2021/manifest.json \
  path/to/weekly-2020-2021/weekly-manifest.json
```

## Independent first-conflict check

For 2021-01-18, all 10,080 raw one-minute opens are continuous. Direct CSV sums,
without using the replay resampler, reproduce the derived volume
`640226.93578500`, quote volume `21440700273.04002577` and trade count
`14699334`. The native weekly row instead contains volume `637026.04205300`,
quote volume `21335137260.47123014` and trade count `14638581`.

The independent check is retained in
`docs/evidence/rules-v0.7-weekly-raw-sum-check.json`. The first discrepancy is
present in the source records, not introduced by summing the replay buckets.
Its upstream cause is not established. Differences must not be dismissed as
rounding, and matching prices do not justify ignoring volume used by analysis.

## Diagnostic correction and next gate

The first workflow's `tee` pipeline masked the audit command's nonzero exit
code, leaving an empty report that failed at JSON verification. The workflow
now explicitly uses Bash pipefail and retains the structured rejection error,
so the actual source-integrity failure is visible at the correct step.

Next: investigate pre-2022 source provenance/revisions or define and freeze a
separate, explicitly justified source-consistency policy before another
readiness experiment. Do not loosen this frozen experiment's exact-match rule,
discard conflicting weeks, optimise a test window or inspect performance.
The official-risk/SEC gate remains a later unresolved step.

`main`, PR #37, deployed resources and secrets were not changed. Sealed
2022–2025 evidence and the 2026 holdout were not accessed in this experiment.
No production dispatch, exchange writes or real trading were enabled.
