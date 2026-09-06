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

## Initial result

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

## Native-monthly authoritative rerun

Run date: 2026-09-06 UTC

- GitHub Actions run:
  [`34030140029`](https://github.com/Labhary/btc-sentinel/actions/runs/34030140029)
- Retained workflow artifact ID: `9988481533` (90-day Actions retention)
- Native monthly dataset:
  `binance-vision-btcusdt-native-monthly-2017-2024-02-v2`
- Native monthly manifest SHA-256:
  `4e12dfc0aced94db8314d9c004247fbe6704816d9563753c71459b754bc6eda6`
- Market and risk datasets, hashes, evaluation range, and strategy version:
  unchanged from the initial attempt

The rerun acquired all 60 official one-minute archives, validated 2,628,362
usable candles, acquired 79 native monthly archives from August 2017 through
February 2024, regenerated the same 140,256 point-in-time risk states, and
evaluated every available boundary. Native monthly data overlays only its
declared range; complete gap-checked one-minute-derived months remain in use
from March 2024 onward.

### Rerun result

- Available completed 15-minute decision boundaries: `140250`
- Created signals: `1`
- Completed out-of-sample fixed/managed pairs: `0`
- Fixed verdict: `INCONCLUSIVE`
- Managed verdict: `INCONCLUSIVE`
- Walk-forward folds: `0`
- Strict win rate: unavailable
- Net and average R: unavailable

The result fails the minimum evidence gates before profitability can be tested:
it has no complete walk-forward fold or out-of-sample trade, versus required
minimums of three folds and 100 out-of-sample trades, including at least 20 in
each bullish and bearish trend regime. The rejection counters overlap because
one boundary can fail several gates; the largest were setup score below 80
(`137871`), non-aligned overall regime (`136986`), unreliable multi-timeframe
analysis (`91205`), and blocking news/risk coverage (`75564`).

This is not a failed >60% result; it is an absence of enough eligible evidence
to calculate a win rate. It does not authorize deployment or paper activation.
The 2022–2025 window is now consumed evidence and must not be tuned against.
Any changed rule set requires a new strategy version, a separately declared
development dataset, and a new untouched holdout before another performance
claim.
