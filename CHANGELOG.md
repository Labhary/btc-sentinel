# Changelog

All notable project changes are recorded here.

## 0.9.0 — 2026-09-02

- Added an atomic append-only Phase 9 statistics snapshot after every fixed or
  managed track close.
- Added separate fixed and managed counts for wins, losses, break-even results,
  early exits, and positive, negative, or flat R outcomes.
- Added strict win rate over every resolved outcome, separately labeled
  decisive win rate, positive-return rate, and a 95% Wilson interval so small
  samples cannot masquerade as reliable performance.
- Added net, average, and median R, profit factor, and chronological maximum
  drawdown measured in R.
- Added completed fixed-versus-managed pair deltas and explicit unresolved fixed
  or managed counts without fabricated outcomes.
- Rejected contradictory outcome labels and R signs atomically, rolling back
  the close and snapshot together.
- Added deterministic Phase 9 tests. No reports, production schedule, signal
  delivery, deployment, private API access, or trading capability was enabled.

## 0.8.0 — 2026-09-02

- Added immutable, versioned Phase 8 decisions from completed Spot BTCUSDT
  one-minute candles, with changes effective only after the evidence candle.
- Added a default 1.5R managed-stop rule using a cost-adjusted break-even price;
  the fixed comparison track keeps its original stop and target.
- Added realized-plus-remaining R accounting for experimental partial exits,
  while leaving partials disabled by default because taking half before TP1
  would lower a successful managed outcome below the original 2R objective.
- Integrated track-specific stops into lifecycle replay, including conservative
  same-candle ordering and independent fixed/managed outcomes.
- Added stable decision deduplication and monotonic checkpoints for restart-safe
  replay after a decision commit.
- Added deterministic Phase 8 tests. No production schedule, signal delivery,
  deployment, private API access, or trading capability was enabled.

## 0.7.0 — 2026-09-02

- Added deterministic replay of completed Binance Spot one-minute candles for
  pending entry, expiry, TP1, and stop lifecycle transitions.
- Added conservative zone-edge fills, adverse opening-price fills when an
  already-active trade gaps through its stop, and cost-aware R accounting.
- Counted a same-candle TP1 and stop as a stop loss, and deferred targets first
  observed on the activation candle because intraminute ordering is unknown.
- Added durable activation timestamps, stable event deduplication keys, and
  monotonic replay checkpoints so interrupted runs resume without inventing
  wins or duplicating outcomes.
- Activated fixed and managed paper tracks together while allowing either track
  to finish independently.
- Added deterministic Phase 7 tests. No schedule, Telegram signal delivery,
  deployment, private API access, or trading capability was enabled.

## 0.6.0 — 2026-09-01

- Added a deterministic Phase 6 signal engine that recomputes Phase 4 analysis
  rather than trusting a caller-provided score.
- Added selective admission gates for a minimum 80/100 evidence score, aligned
  directional trend, complete ordered timeframe hierarchy, aligned one-hour
  and 15-minute execution context, input freshness, active trade, and cooldown.
- Added structure-zone entries, ATR-buffered stops, four-hour expiry, and
  conservative zone-edge fill assumptions.
- Added 2.25R and 3.25R targets net of modeled 0.15% round-trip costs, with
  rejection when one-hour structure obstructs TP1.
- Integrated Phase 5 as a strict risk gate: blocking news rejects a candidate,
  while caution cannot choose direction and only reduces suggested paper risk.
- Reduced suggested paper risk when optional derivatives context is degraded.
- Added 28 deterministic Phase 6 tests. No persistence, scheduling, Telegram
  delivery, deployment, private API access, or trading capability was enabled.

## 0.5.0 — 2026-09-01

- Added fixed, unauthenticated HTTPS collection for official Federal Reserve,
  SEC, BLS, and Coinbase status sources, with optional GDELT discovery.
- Added bounded RSS/Atom, iCalendar, and JSON parsing; redirect rejection;
  response-size limits; bounded retries; and independent source failures.
- Added deterministic relevance, category, direction, volatility,
  deduplication, official confirmation, cross-domain corroboration, and 0–100
  reliability scoring.
- Added conservative scheduled macro-event windows and market-confirmation
  waits after verified high-impact news.
- Added fail-closed required-source coverage and explicit degraded handling for
  optional sources.
- Enforced that news can only block or delay a setup and can never create a
  signal, entry, stop, target, or trading direction.
- Added 47 deterministic Phase 5 tests. No schedules, deployment, private API
  access, or trading permissions were enabled.

## 0.4.0 — 2026-09-01

- Added dependency-free completed-candle EMA, RSI, MACD, ADX, ATR, Bollinger,
  rolling VWAP, volume, and relative-volatility calculations.
- Added deterministic swing structure, break/change context, and bounded
  support/resistance zones.
- Added monthly-through-15-minute hierarchical regime and directional analysis.
- Added fixed evidence-group weights that prevent correlated trend indicators
  from being counted as independent evidence.
- Added explicit no-trade outcomes for major-timeframe conflicts, missing or
  incomplete required data, and abnormal relative volatility.
- Added 24 deterministic Phase 4 tests. No signals, deployment, or trading
  permissions were enabled.
- Refreshed Wrangler and Cloudflare type development dependencies to remove
  newly disclosed transitive `nanoid` and `undici` vulnerabilities; Worker
  source behavior is unchanged.

## 0.3.0 — 2026-08-02

- Added strict unauthenticated Binance Spot and USD-M futures clients for BTCUSDT.
- Added immutable candle, funding, open-interest, taker-volume, and order-book models.
- Added completed-candle filtering, continuity, UTC alignment, freshness, and
  cross-source price and clock validation.
- Added bounded HTTP timeouts, response sizes, retries, exponential backoff,
  rate-limit handling, redirect rejection, and short-lived request coalescing.
- Added a fail-closed coherent snapshot collector with explicit degradation for
  optional historical and order-book context.
- Documented public endpoint roles, retention limits, and why liquidation
  snapshots are excluded from deterministic historical evidence.
- Added deterministic parsing and failure-mode tests for the market-data engine.

## 0.2.0 — 2026-08-02

- Added the Cloudflare command Worker foundation.
- Added webhook-secret validation and private owner authorization.
- Added `/start`, `/help`, `/status`, `/pause`, and `/resume`.
- Added durable Telegram update leasing, command audit, and outbox delivery states.
- Added explicit uncertain-delivery handling to avoid blind duplicate messages.
- Added replayable Phase 2 migration and Worker failure-mode tests.
- Added an independent TypeScript CI job.

## 0.1.0 — 2026-08-02

- Added Phase 1 architecture and requirements review.
- Added BTC-only domain records and lifecycle state machine.
- Added immutable SQLite/D1 persistence schema.
- Added fixed-versus-managed trade-track model.
- Added environment validation, timezone conversion, and secret redaction.
- Added tests, CI, and repository secret scanning.
