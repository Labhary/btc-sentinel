# Phase 12 deployment readiness

Phase 12 is **prepared but not activated**. This repository now contains the
Cloudflare/D1 boundary, five-minute dispatch plumbing, signed health API,
deterministic orchestration core, typed runtime bootstrap, durable notification
enqueue, and a GitHub Actions workflow scaffold. It deliberately refuses to run
a production paper engine because the typed D1 repository mutation adapter and
atomic signal/outbox commit are not implemented.

Deploying the current branch would therefore be premature. A green CI run
proves configuration and boundary behavior; it does not prove a running bot.

## Safety gates

Two independent values default to `false`:

- `PRODUCTION_DISPATCH_ENABLED` is a non-secret Cloudflare Worker variable. The
  Cron Trigger is a no-op while it is false.
- `PAPER_ENGINE_ENABLED` is a GitHub Actions repository variable. The workflow
  remains a readiness-only no-op while it is false and hard-fails if someone
  changes it to true before the durable repository adapter exists.

The hard-coded runtime gate may be replaced only after the orchestrator has an
integrated and tested typed D1 repository adapter. Merely changing variables
cannot bypass it.

## Implemented boundary

- Cloudflare's UTC Cron expression is `*/5 * * * *`.
- Each scheduled instant has a durable unique dispatch key in D1. Duplicate or
  uncertain attempts are not blindly replayed.
- Workflow dispatch is fixed to `Labhary/btc-sentinel`, `paper-engine.yml`, and
  `main`; callers cannot redirect it to another host, repository, workflow, or
  ref.
- The state API accepts only `/state/v1/bootstrap`, `/state/v1/notifications`,
  and `/state/v1/health`. Bootstrap returns bounded monitor/history state;
  notifications accept four fixed paper-message types and inject the configured
  owner identity inside the Worker. HMAC-SHA256 covers method, exact path,
  timestamp, nonce, and raw-body SHA-256.
- Requests outside a five-minute clock window, reused nonces, query strings,
  oversized bodies, unknown fields, and invalid health records fail closed.
- The Python client rejects redirects, non-HTTPS origins, oversized or malformed
  responses, and non-success HTTP status codes.

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

1. Implement and test the typed D1 repository adapter used by the orchestration
   core, including an atomic idempotent signal-plus-outbox commit.
2. Run a representative exhaustive historical backtest. Treat a failed or
   inconclusive result as a stop condition, not as permission to tune the same
   test window.
3. Create a D1 database and replace the placeholder database ID in a private
   `worker/wrangler.toml` file.
4. Apply migrations 1–3 to a preview D1 database and verify replayability.
5. Add encrypted secrets through Wrangler/GitHub settings and deploy a preview
   with dispatch still disabled.
6. Verify `/health`, signed bootstrap/health writes, webhook ownership checks,
   duplicate dispatch behavior, and D1 audit rows.
7. Register the Telegram webhook only after the preview checks pass.
8. Enable paper observation with both gates under a reviewed change. Never add
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
