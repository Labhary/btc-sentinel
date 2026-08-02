# BTC Sentinel command Worker

This Worker is the small, immediate Telegram and D1 boundary. It does not
calculate indicators or generate signals.

## Phase 2 behavior

- `GET /health` returns a minimal non-secret health response.
- `POST /telegram/webhook` accepts Telegram updates only with the configured
  webhook secret header.
- Commands are accepted only from the configured numeric owner ID in a private
  chat.
- Duplicate Telegram `update_id` values are ignored durably.
- `/start`, `/help`, `/status`, `/pause`, and `/resume` are implemented.
- Every command response uses an outbox row with `PENDING`, `UNKNOWN`, `SENT`,
  or `FAILED` delivery state.

`UNKNOWN` is intentional: if a network request may have reached Telegram but
the response was lost, the Worker does not blindly resend and create a duplicate.

## Local checks

```bash
npm ci
npm run format:check
npm run typecheck
npm test
```

Deployment stays disabled until the D1 database and encrypted secrets are
created in the deployment phase.
