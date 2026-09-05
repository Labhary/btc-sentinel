# Representative backtest record: 2022–2025

Run date: 2026-09-05 UTC

This record preserves the first complete conservative official-archive attempt.
It is not a profitability claim and it must not be replaced by a hand-picked
subset.

## Immutable inputs

- Market dataset: `binance-vision-btcusdt-2021-2025-v1`
- Market manifest SHA-256:
  `1fdde125880f285f85c69d2c5426d769fbfac92767b8dbb79a9f50241741e341`
- Risk dataset: `official-risk-2022-2025-v1`
- Risk manifest SHA-256:
  `534d2159010089cd8809e3c3f46ac41d427a49da33dacff973b7b311d2a3bb15`
- Evaluation range: `[2022-01-01T00:00:00Z, 2026-01-01T00:00:00Z)`
- Strategy version: `rules-v0.6.0`

The market input contained 60 official monthly one-minute archives and
2,628,367 raw rows. Five suspect early-close rows were excluded and eight
missing intervals were explicitly ledgered, leaving 2,628,362 usable completed
one-minute candles. The risk input retained 99 raw official Fed/SEC/BLS pages,
1,067 normalized records, 14 conservative coverage gaps, and 140,256 exact
15-minute risk states.

## Result

- Available completed 15-minute decision boundaries: `140250`
- Created signals: `0`
- Fixed verdict: `INCONCLUSIVE`
- Managed verdict: `INCONCLUSIVE`
- Walk-forward folds: `0`
- Resolved trades: `0`
- Strict win rate: unavailable

All 140,250 candidates lacked the fixed 50-candle monthly analysis history.
The reason is not insufficient calendar duration: a handful of declared
one-minute outages caused their containing derived monthly candles to be
discarded, and the strict contiguous-series rule correctly refused to bridge
those holes. Risk coverage independently blocked 75,564 boundaries; that fact
does not change the primary market-history failure.

## Approved remediation

Do not reduce the 50-candle requirement, interpolate missing minutes, accept
discontinuous EMA input, or tune against this evaluation period. Use Binance's
official native `1mo` archives as a separate checksum-bound monthly input,
starting in 2017 for pre-2022 warm-up. Keep the one-minute archive as the sole
execution and fill path. Rerun the same frozen strategy and evaluation window,
then append—not overwrite—the resulting verdict.
