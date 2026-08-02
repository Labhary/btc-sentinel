# Changelog

All notable project changes are recorded here.

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
