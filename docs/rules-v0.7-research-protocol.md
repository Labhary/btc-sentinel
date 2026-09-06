# Rules v0.7 research protocol

This protocol prevents the search for a high win rate from turning historical
validation into curve fitting. It does not claim that rules v0.7 is profitable
or that it can exceed a 60% strict win rate at 2R.

## Evidence partitions

| Period | Role | Permitted use |
|---|---|---|
| 2017-08 through 2021-12 | Development and warm-up | Data-integrity checks and development diagnostics only |
| 2022-01 through 2025-12 | Sealed rules-v0.6 evaluation | Historical record only; never select or tune v0.7 rules |
| 2026-01 onward | Untouched v0.7 holdout | One evaluation after the v0.7 strategy commit is frozen |

Binance Spot BTCUSDT began during 2017, and every required timeframe needs 50
completed candles. Consequently, the native monthly requirement leaves only a
short pre-2022 interval with complete warm-up. Any development report must show
the effective evaluated interval and may not disguise warm-up rejections as
market observations.

## Frozen v0.7 change

Rules v0.7 makes one semantic scoring correction: setup agreement is normalized
over evidence weights that are actually available. The optional derivatives
group remains a 10-point group when it is observed. When it is unavailable, it
does not lower the maximum score from 100 to 90; the analysis remains degraded
and later recommends reduced paper risk. Observed neutral or conflicting
derivatives evidence stays in the denominator and can lower the score.

This change is justified by the meaning of an optional evidence group, not by a
chosen historical profit result. Thresholds, indicators, regime rules, entry,
stop, targets, costs, cooldown, expiry, lifecycle fills, and management remain
unchanged.

## Holdout rules

Before reading any 2026 performance result:

1. merge the exact strategy and protocol commit;
2. record its commit SHA and immutable input-manifest hashes;
3. run every 15-minute boundary with official checksum-bound inputs;
4. replay thresholds 75, 80, and 85 as independent state paths;
5. retain the complete machine-readable report even if it is inconclusive or
   failed.

After the first 2026 report is read, rules v0.7 is closed to further tuning on
that period. Any subsequent rule change requires rules v0.8 and a new untouched
future holdout. Deployment and Phase 13 remain disabled unless all predeclared
sample, regime, uncertainty, sensitivity, cost, and 2R gates pass.
