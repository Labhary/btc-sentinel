# Phase 12 deployment readiness

Phase 12 is **prepared but not activated**. This repository now contains the
Cloudflare/D1 boundary, five-minute dispatch plumbing, signed health API,
deterministic orchestration core, typed runtime bootstrap, durable notification
enqueue, typed D1 repository mutations, atomic signal/outbox persistence,
bounded pending-outbox recovery, and a GitHub Actions workflow scaffold. It
also contains the executable job assembly for the public collectors, typed
repository, orchestrator, notification recovery, and health sink. It deliberately
refuses to run until historical validation and the remaining preview and
provisioning steps are complete.

The historical path now includes immutable archive preflight plus a
disk-backed, point-in-time multi-timeframe Spot candle index and an exhaustive
streaming signal/lifecycle runner. The runner blocks when historical news/macro
coverage is missing. A checksum-bound continuous risk-timeline store now
enforces exact-time evidence and required-source failures, but no representative
representative source archive has been replayed. The complete historical command is
wired, and the public monthly Binance Vision market archive can now be acquired
without credentials. The historical risk timeline can now be derived from
checksum-bound normalized official-source evidence. The conservative archive
builder now downloads and retains the raw Federal Reserve, SEC, and BLS pages,
binds normalized records to those hashes, and turns uncertain BLS schedule
availability—including a missing last-modified date—into blocking gaps. A live
2024 smoke reconstruction passed, but
the predeclared 2022–2025 representative replay has not run. This is engineering
readiness, not performance evidence.

Deploying the current branch would therefore be premature. A green CI run
proves configuration and boundary behavior; it does not prove a running bot.

## Safety gates

Two independent values default to `false`:

- `PRODUCTION_DISPATCH_ENABLED` is a non-secret Cloudflare Worker variable. The
  Cron Trigger is a no-op while it is false.
- `PAPER_ENGINE_ENABLED` is a GitHub Actions repository variable. The workflow
  remains a readiness-only no-op while it is false and hard-fails if someone
  changes it to true before the full runtime is approved.

The hard-coded runtime gate may be replaced only after representative backtest
validation, preview checks, and an explicit activation review. Merely changing
variables cannot bypass it.

## Implemented boundary

- Cloudflare's UTC Cron expression is `*/5 * * * *`.
- Each scheduled instant has a durable unique dispatch key in D1. Duplicate or
  uncertain attempts are not blindly replayed.
- Workflow dispatch is fixed to `Labhary/btc-sentinel`, `paper-engine.yml`, and
  `main`; callers cannot redirect it to another host, repository, workflow, or
  ref.
- The state API accepts only fixed typed bootstrap, notification, outbox-drain,
  health, and repository operations. Bootstrap returns bounded monitor/history state;
  notifications accept four fixed paper-message types and inject the configured
  owner identity inside the Worker. HMAC-SHA256 covers method, exact path,
  timestamp, nonce, and raw-body SHA-256.
- Requests outside a five-minute clock window, reused nonces, query strings,
  oversized bodies, unknown fields, and invalid health records fail closed.
- The Python client rejects redirects, non-HTTPS origins, oversized or malformed
  responses, and non-success HTTP status codes.
- The Python job requires an explicit valid dispatch identity, production mode,
  the fixed BTCUSDT/Casablanca baseline, frozen 2R and risk settings, and a
  separate state-API origin and signing secret. It logs only bounded summaries.

## Secrets and permissions

Never commit or paste these values into an issue, log, or chat:

| Location | Secret | Minimum purpose |
| --- | --- | --- |
| Cloudflare | `TELEGRAM_BOT_TOKEN` | Send owner-only Telegram messages |
| Cloudflare | `TELEGRAM_ADMIN_USER_ID` | Authorize the single owner |
| Cloudflare | `TELEGRAM_WEBHOOK_SECRET` | Authenticate Telegram webhooks |
| Cloudflare and GitHub | `STATE_API_HMAC_SECRET` | Sign the narrow state API |
| Cloudflare | `GITHUB_ACTIONS_TOKEN` | Actions write for this repository only |
| GitHub | `CLOUDFLARE_API_TOKEN` | Future manual deployment workflow only |
| GitHub | `CLOUDFLARE_ACCOUNT_ID` | Select the deployment account |

The GitHub token used by the Worker must be fine-grained, scoped only to this
repository, and granted only the Actions permission needed to dispatch a
workflow. No Binance key exists or is required.

## Future activation sequence

These steps are intentionally **not performed by this pull request**:

1. Validate the complete immutable Binance Vision archive manifest, then run a
   representative exhaustive historical backtest. Treat a failed or
   inconclusive result as a stop condition, not as permission to tune the same
   test window. A successful archive preflight is not a performance verdict.
2. Create a D1 database and replace the placeholder database ID in a private
   `worker/wrangler.toml` file.
3. Apply migrations 1–4 to a preview D1 database and verify replayability.
4. Add encrypted secrets through Wrangler/GitHub settings and deploy a preview
   with dispatch still disabled.
5. Verify `/health`, signed bootstrap/health writes, webhook ownership checks,
   duplicate dispatch behavior, and D1 audit rows.
6. Register the Telegram webhook only after the preview checks pass.
7. Enable paper observation with both gates under a reviewed change. Never add
   exchange order permissions.

Cloudflare documents that Cron Triggers execute in UTC and may take time to
propagate. Its D1 migration command applies pending migrations with rollback on
failure and creates a backup. GitHub documents that workflow dispatch requires
the target workflow on the selected ref and an Actions-write fine-grained
credential. Recheck these official requirements and free-tier limits at actual
activation time:

- <https://developers.cloudflare.com/workers/configuration/cron-triggers/>
- <https://developers.cloudflare.com/d1/wrangler-commands/#d1-migrations-apply>
- <https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event>
