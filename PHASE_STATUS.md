# Project status

Last updated: 2026-08-02

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

## Next — Phase 4

- Completed-candle indicators.
- Market structure and deterministic support/resistance zones.
- Hierarchical monthly-to-15-minute regime classification.
- Evidence-group scoring without double counting.

## Not implemented yet

Deployment is not active. Indicators, regime classification, news ingestion,
signal generation, live lifecycle monitoring, reports, statistics, and
backtesting remain disabled until their phases are implemented and tested.
