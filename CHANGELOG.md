# Changelog

All notable project changes are recorded here.

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
