# BTC Sentinel command Worker

This Worker is the small, immediate Telegram and D1 boundary. It does not
calculate indicators or generate signals.

## Runtime boundary

- `GET /health` returns a minimal non-secret health response.
- `POST /telegram/webhook` accepts Telegram updates only with the configured
  webhook secret header.
- Commands are accepted only from the configured numeric owner ID in a private
  chat.
- Duplicate Telegram `update_id` values are ignored durably.
- `/start`, `/help`, `/status`, `/pause`, and `/resume` are implemented.
- Every command response uses an outbox row with `PENDING`, `UNKNOWN`, `SENT`,
  or `FAILED` delivery state.
- Signed bootstrap, health, notification, bounded outbox recovery, and typed
  repository operations use timestamped, nonce-protected HMAC authentication.
- Signal creation commits its immutable terms, targets, audit event, and owner
  notification outbox row in one D1 batch.
- A five-minute UTC Cron Trigger can dispatch one fixed GitHub workflow, with a
  durable D1 deduplication key and bounded failure codes.

`UNKNOWN` is intentional: if a network request may have reached Telegram but
the response was lost, the Worker does not blindly resend and create a duplicate.

## Local checks

```bash
npm ci
npm run format:check
npm run typecheck
npm test
```

`PRODUCTION_DISPATCH_ENABLED` defaults to `false`. Executable job assembly and
deployment validation are not complete, so the runtime gate refuses activation
even if a variable is changed accidentally. See `docs/deployment-readiness.md`
before provisioning anything.
