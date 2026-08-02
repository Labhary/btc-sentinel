# ADR 0001: Hybrid free runtime

- Status: accepted
- Date: 2026-08-02

## Decision

Use Python 3.12 on GitHub Actions for analysis/backtesting and a small
Cloudflare Worker plus D1 for workflow dispatch, instant commands, and durable
state.

## Reasons

- Temporary runner disks cannot be the source of truth.
- Native GitHub scheduled workflows can be delayed and quiet public repository
  schedules are disabled after prolonged inactivity. A lightweight Cloudflare
  cron therefore dispatches the workflow; every job still replays from a
  durable checkpoint and remains idempotent.
- GitHub-only Telegram polling would make phone commands wait for the next job.
- Cloudflare's free Python Worker runtime is beta and the free CPU budget is too
  small for dependable multi-timeframe analysis.
- D1 has SQLite semantics, HTTP/Worker access, transactions, recovery, and a
  free tier suitable for compact bot state.

## Consequences

- There are two deployable components.
- The Worker must expose a narrow authenticated API; arbitrary remote SQL is
  forbidden.
- A public repository is needed for sustainably free frequent standard GitHub
  Actions. Secrets remain private.
- The bot is candle-based paper monitoring, not a real-time trading executor.
