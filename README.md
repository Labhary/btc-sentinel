# BTC Sentinel

BTC Sentinel is a BTC/USDT-only market-analysis and paper-trading alert bot. It
is being built as a deterministic, auditable system: market levels come from
market data and rules, not from a language model.

> **Current status:** Phases 1–11 are implemented and tested. The project now has
> an owner-only Telegram command foundation, a strict Binance public-data engine,
> deterministic multi-timeframe analysis, and a fail-closed news/macro risk
> filter. Phase 7 can persist and conservatively reconstruct paper-signal
> lifecycles from completed one-minute candles. Phase 8 adds versioned managed
> paper-position decisions while preserving an unchanged fixed comparison.
> Phase 9 creates strict append-only statistics after every track close, and
> Phase 10 prepares read-only paper reports with sample sizes and uncertainty.
> Phase 11 adds conservative fixed/managed replay and purged walk-forward
> evaluation. No representative historical dataset has been evaluated, so the
> strict win-rate objective above 60% at 2R or better remains unproven.
> Phase 12 deployment boundaries, deterministic orchestration core, typed D1
> repository adapter, atomic signal/outbox commit, and executable GitHub Actions
> job assembly are implemented and remain hard-disabled. Representative backtest
> validation, resource provisioning, preview verification, and deployment are
> not complete.
> A streaming, checksum-bound Binance Vision historical-data preflight is now
> available. A disk-backed replay index now creates complete-only point-in-time
> 15m through monthly Spot views, and an exhaustive runner streams signal and
> lifecycle evaluation without future leakage. A checksum-bound 15-minute
> historical risk-timeline store rejects future evidence and required-source
> gaps that do not block. An executable command now joins both immutable inputs
> through exhaustive replay and separate fixed/managed verdicts. No
> representative market/risk dataset has been run, so the strategy has not
> passed a backtest.
> This must not yet be treated as a live trading tool.

## Non-negotiable behavior

- BTC/USDT only.
- Analysis and paper tracking only; no exchange order permissions.
- UTC internally; `Africa/Casablanca` in user-facing messages.
- No promised win rate or guaranteed profitability.
- A setup must offer at least 2R to its first planned target after modeled
  costs before it can become a signal.
- Missing, stale, or contradictory required data produces `NO TRADE`.
- Original terms and audit events are immutable.
- Managed and unchanged/fixed trade paths are tracked independently.

## Chosen free architecture

The planned production architecture is intentionally hybrid:

1. **Python 3.12 engine in GitHub Actions** for candle-based analysis,
   lifecycle reconstruction, reports, tests, and backtests.
2. **Cloudflare Worker** for immediate Telegram commands, the five-minute
   workflow dispatch, and a narrow authenticated state API.
3. **Cloudflare D1** for durable relational state and the append-only audit
   trail.
4. **Telegram Bot API** for the iPhone-facing interface.
5. **Binance public APIs only** for BTC/USDT market data in version 1.

The Python job will evaluate completed candles. If a scheduled run is late or
missed, it will replay closed one-minute candles from the last durable
checkpoint. This makes paper-trade accounting recoverable, but it does not make
GitHub Actions a real-time execution platform.

See [architecture.md](docs/architecture.md), [data-sources.md](docs/data-sources.md),
[news-risk.md](docs/news-risk.md), [signals.md](docs/signals.md),
[lifecycle.md](docs/lifecycle.md), [position-management.md](docs/position-management.md),
[statistics.md](docs/statistics.md), [reports.md](docs/reports.md),
[backtesting.md](docs/backtesting.md), [deployment-readiness.md](docs/deployment-readiness.md), and
[requirements-review.md](docs/requirements-review.md) for the trade-offs and
hard limits.

## Repository map

```text
src/btc_sentinel/       Python domain, data, analysis, signal, and lifecycle engines
migrations/             SQLite/D1-compatible migrations
tests/                  Python domain and migration tests
worker/                 Telegram command Worker and TypeScript tests
scripts/                Repository safety checks
docs/                   Architecture, schema, security, roadmap
.github/workflows/      CI and hard-disabled paper-runtime readiness workflow
```

## Local validation

Python 3.12 is required.

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
pytest
ruff check .
python scripts/check_no_secrets.py
```

The tests also use only Python's standard `unittest` assertions, so the core
suite can be run without downloading pytest:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
btc-sentinel-validate-history path/to/manifest.json
btc-sentinel-validate-risk-history path/to/risk-manifest.json
btc-sentinel-run-history market.json risk.json START_UTC END_UTC
```

The command Worker is checked separately:

```bash
cd worker
npm ci
npm run format:check
npm run typecheck
npm test
```

Deployment stays intentionally disabled until historical validation and every
preview-readiness check are complete. The Worker currently supports `/start`, `/help`,
`/status`, `/pause`, and `/resume` for one configured owner in a private chat.

## Secrets

Never put real values in `.env`, source files, screenshots, issues, or Telegram
messages. `.env.example` contains names and placeholders only. Production
values will live in GitHub Secrets and Cloudflare encrypted secrets.

If a Telegram token is ever exposed, revoke it immediately with BotFather,
create a replacement, update the secret stores, and review recent activity.

## Disclaimer

This project is research software for analysis and paper trading. It is not
financial advice, does not guarantee any result, and is not ready for real-money
execution.
