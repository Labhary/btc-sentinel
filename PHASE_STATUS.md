# Project status

Last updated: 2026-09-01

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

## Next — Phase 6

- Conservative signal admission from validated Phase 4 analysis context.
- Entry, stop, targets, modeled costs, minimum 2R, expiry, and cooldown.
- News-risk approval as a required gate, never a signal source.

## Not implemented yet

Deployment is not active. Signal generation, live lifecycle monitoring,
reports, statistics, and backtesting remain disabled until their phases are
implemented and tested. Phase 5 collection is implemented as a library but no
production schedule or external write is enabled.
