# Backtesting policy

Phase 11 supplies a deterministic backtesting framework. It does **not** ship a
representative historical dataset, and no real performance verdict has been
earned yet. The current >60% strict win-rate objective at 2R or better is
therefore **unproven**, not passed.

## Dataset contract

A performance run must declare an immutable dataset identifier, UTC coverage
range, strategy version, included sources, excluded features, and fill-policy
version. It must include completed Binance Spot BTCUSDT one-minute candles and
must exhaustively evaluate every candidate decision time in the declared
period. Hand-picked signals are rejected by policy.

Inputs must be information that was available at each historical decision
time. Features without reliable point-in-time history, such as snapshots of
the current order book or liquidation feed, must be excluded and named in the
run metadata; they must never be reconstructed from later information.

The repository tests use synthetic cases only to prove mechanics and failure
handling. Synthetic test results are not evidence of profitability.

## Historical archive preflight

Version `0.12.4` adds a streaming validator for Binance Vision Spot
`BTCUSDT` one-minute ZIP archives. It verifies an immutable JSON manifest,
SHA-256 for every archive, one expected CSV member per ZIP, exact 12-field
klines, decimal values, row counts, UTC coverage, and minute-by-minute
continuity. ZIP traversal, encryption, unsafe compression ratios, oversized
inputs, duplicate JSON fields, unknown manifest fields, and checksum changes
are rejected.

Run the preflight without loading the multi-year dataset into memory:

```bash
btc-sentinel-validate-history path/to/manifest.json
```

The manifest records `schema_version`, `dataset_id`, fixed Spot `BTCUSDT`/`1m`
identity, exclusive UTC coverage, `https://data.binance.vision` as the source
origin, `exhaustive_candidate_scan: true`, excluded features, and ordered
archives. Each archive records its safe relative path, a fixed Binance Vision
Spot URL with the same filename, lowercase SHA-256, timestamp unit, exclusive
UTC coverage, and exact row count. Timestamp units are explicit because
Binance documents microsecond Spot timestamps from 2025-01-01 onward; the
validator never guesses the unit.

A successful preflight reports `performance_verdict: NOT_RUN`. Data integrity
is not strategy evidence.

## Point-in-time candle index

Version `0.12.5` streams a validated manifest into a disk-backed SQLite replay
index. It preserves the source one-minute candles and creates exact completed
15-minute, one-hour, four-hour, daily, weekly, and monthly Spot views. Weeks
start Monday at 00:00 UTC; months start on their first calendar day at 00:00
UTC. An aggregate is discarded unless the source covers both the exact start
and exact end of that interval, so partial archive boundaries cannot be
mistaken for complete candles.

Every query is evaluated at an explicit historical `as_of` time and returns
only candles whose close is strictly earlier. Exhaustive candidate times come
from completed 15-minute boundaries. Imports are transactional: a late hash,
continuity, coverage, or row failure rolls back every inserted candle and all
metadata.

This index supplies historical Spot candle views only. It does not invent
unavailable point-in-time order books, derivatives history, news, or macro
events, and it has not yet executed the strategy or produced a performance
verdict. Point-in-time news coverage, full signal/lifecycle replay,
walk-forward evaluation, and the policy gates below must still run before any
performance conclusion is possible.

## Conservative simulation

- Only completed, continuous Spot BTCUSDT one-minute candles are accepted.
- Replay begins at the first full minute that could safely follow signal
  creation; look-ahead candles are rejected.
- Entry is modeled at the adverse edge of the entry zone.
- If stop and target are both touched in one candle, the stop wins the
  ambiguity. A target first seen in the activation candle is deferred because
  intraminute order is unknown.
- A gap through a stop fills at the worse candle open.
- Expired, unfilled signals remain `NO_FILL`; trades still open at the dataset
  boundary remain `UNRESOLVED`. Neither is fabricated into a win or loss.
- Round-trip costs are included in R results and are stressed at 1x, 1.5x, and
  2x the configured estimate.

The fixed track preserves the original stop and target. The managed track
replays the default Phase 8 rule: after a completed non-activation candle closes
at or above 1.5R, a cost-adjusted break-even stop becomes effective on the next
candle. Both variants use the same candidate cases and walk-forward windows.
Completed pairs report their managed-minus-fixed R delta; unresolved outcomes
remain explicitly counted.

## Walk-forward evaluation

The default chronological policy uses 120 candidate cases for training, a
four-case purge gap, and 40 cases for each non-overlapping test window. The
primary score threshold is fixed in advance at 80. Thresholds 75, 80, and 85
are reported as sensitivity checks rather than chosen after seeing test data.

A run is `INCONCLUSIVE` unless it contains at least:

- three complete walk-forward folds;
- 30 resolved training trades in each fold;
- 100 resolved out-of-sample trades at the primary threshold; and
- 20 out-of-sample trades in each bullish and bearish trend regime.

After those minimums are met, a variant is `PASSED` only when all of these hold:

- observed strict win rate is greater than 60%;
- the 95% Wilson lower confidence bound is also greater than 60%;
- out-of-sample average R and net R are positive;
- every selected setup planned at least 2R;
- score-threshold sensitivity does not show negative expectancy where it has
  enough observations; and
- average expectancy remains positive at every configured cost multiplier.

Otherwise the result is `FAILED`, with reasons recorded. Break-even and early
exit results remain part of the strict win-rate denominator.

## Change control

The out-of-sample windows are evidence, not a tuning surface. Changing a
threshold, indicator, fill rule, management rule, or cost assumption after
examining those results requires a new strategy or policy version and a new
untouched evaluation period. Phase 13 paper observation remains necessary even
after a passing historical run; neither stage authorizes real trading.
