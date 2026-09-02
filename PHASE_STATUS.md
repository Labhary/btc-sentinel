# Project status

Last updated: 2026-09-02

## Completed — Phase 1

- Requirements contradictions and free-tier limitations documented.
- Final free architecture selected.
- BTC-only validated domain model created.
- Lifecycle normalized into states, immutable events, and two trade tracks.
- SQLite/D1-compatible durable schema created.
- Append-only protections and duplicate keys added.
- Secure environment configuration and log redaction added.
- CI and secret-scanning baseline added.
- Automated Phase 1 tests added and executed.

## Completed — Phase 2

- Cloudflare command Worker foundation.
- Telegram webhook validation.
- Admin-only `/start`, `/help`, `/status`, `/pause`, and `/resume`.
- Lease-based durable Telegram update deduplication.
- Durable `PENDING`/`UNKNOWN`/`SENT`/`FAILED` delivery states.
- Append-only command audit.
- Local fake Telegram transport and failure-mode tests.
- Independent Python and Worker CI jobs.

## Completed — Phase 3

- Unauthenticated Binance public clients restricted to BTCUSDT.
- Spot and USD-M futures candles normalized into strict immutable records.
- Monthly-through-15-minute analysis inputs plus one-minute lifecycle inputs.
- Closed-candle, continuity, UTC-alignment, freshness, and source-coherence checks.
- Funding, current and historical open interest, taker volume, and low-weight
  spot order-book snapshots.
- Bounded timeouts, response sizes, retries, backoff, rate-limit handling, and
  in-run request coalescing.
- Fail-closed required inputs and explicit degradation for optional context.
- Deterministic fixtures and malformed, stale, contradictory, gap, outage, and
  rate-limit tests.

## Completed — Phase 4

- Completed-candle EMA, RSI, MACD, ADX, ATR, Bollinger, rolling VWAP, and
  volume calculations using dependency-free Decimal arithmetic.
- Deterministic swing structure, break/change detection, and bounded
  support/resistance zones.
- Hierarchical monthly-to-15-minute regime and directional-bias analysis.
- Five evidence groups with fixed weights so correlated indicators are not
  counted as independent votes.
- Fail-closed conflict, incomplete-candle, missing-timeframe, and abnormal
  volatility behavior.
- Setup-quality scoring explicitly modeled as agreement, never win probability.

## Completed — Phase 5

- Fixed HTTPS sources for Federal Reserve and SEC releases, BLS scheduled
  economic events, Coinbase incidents, and optional GDELT discovery.
- Bounded, unauthenticated RSS/Atom, iCalendar, and JSON collection with no
  redirects and independent required/optional coverage tracking.
- Deterministic relevance, category, direction, volatility, deduplication,
  official/corroborated confirmation, and explicit reliability scores.
- Conservative scheduled-event windows and post-headline market-confirmation
  waits, with fail-closed required coverage.
- News modeled only as a risk filter; it cannot create a signal or trade bias.

## Completed — Phase 6

- Phase 4 analysis is recomputed from the supplied snapshot before admission;
  caller-provided scores are never trusted.
- Selective trend, score, hierarchy, execution-alignment, freshness, active
  trade, and four-hour cooldown gates.
- Structure/ATR entry and stop construction with conservative zone-edge fills.
- Two deterministic targets at least 2.25R and 3.25R net of modeled round-trip
  costs, plus one-hour obstacle rejection.
- Blocking news rejects a setup; caution and degraded optional market context
  reduce suggested paper risk without choosing direction.
- Immutable pending paper-signal output only; no persistence, alert delivery,
  lifecycle monitoring, deployment, or order capability.

## Completed — Phase 7

- Completed Spot one-minute candle replay for deterministic entry, expiry,
  target, and stop reconstruction.
- Conservative zone-edge activation, adverse stop-gap fills, and TP1 terminal
  accounting net of modeled round-trip costs.
- Stop-first loss accounting when TP1 and stop occur in the same candle, with
  activation-candle targets deferred because event order is unknowable.
- Durable activation timestamps, stable event keys, monotonic checkpoints, and
  restart-safe replay.
- Parallel fixed and managed tracks activated and resolved independently.

## Completed — Phase 8

- One immutable, versioned managed-position decision per completed candle.
- Decisions become effective only after the evidence candle closes, preventing
  same-candle hindsight.
- Default 1.5R protection moves only the managed stop to cost-adjusted
  break-even; fixed terms never change.
- Correct realized-plus-remaining R accounting for explicitly enabled partials,
  with partial exits disabled in the default policy because they reduce TP1 R.
- Stable decision keys and monotonic checkpoints recover safely after crashes.

## Completed — Phase 9

- Atomic append-only statistics snapshot after every fixed or managed close.
- Strict and decisive win rates, positive-return rate, 95% Wilson uncertainty,
  net/average/median R, profit factor, and maximum drawdown in R.
- Fixed and managed metrics calculated separately with completed-pair deltas.
- Unresolved fixed and managed comparisons remain visible instead of receiving
  fabricated outcomes.
- Contradictory outcome categories and R signs roll back the close transaction.

## Next — Phase 10

- Daily, weekly, monthly, active, pending, and news-risk reports.
- Render strict statistics with sample size and uncertainty labels.
- Prepare Telegram payloads without enabling production delivery.

## Not implemented yet

Deployment is not active. Phase 7 provides a local lifecycle replay library;
no production monitor or schedule invokes it. Phase 8 management is likewise a
library, not an active service. Phase 9 statistics are durable but no report or
production delivery consumes them yet. Telegram signal delivery, reports, and
backtesting remain disabled until their phases are implemented and tested. No
external write is enabled.
