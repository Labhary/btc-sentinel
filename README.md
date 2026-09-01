# BTC Sentinel

BTC Sentinel is a BTC/USDT-only market-analysis and paper-trading alert bot. It
is being built as a deterministic, auditable system: market levels come from
market data and rules, not from a language model.

> **Current status:** Phases 1–6 are implemented and tested. The project now has
> an owner-only Telegram command foundation, a strict Binance public-data engine,
> deterministic multi-timeframe analysis, and a fail-closed news/macro risk
> filter. Phase 6 can construct selective pending paper-signal records, but
> lifecycle monitoring, delivery, and deployment remain disabled. This must not
> yet be treated as a live trading tool.

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
[news-risk.md](docs/news-risk.md), [signals.md](docs/signals.md), and
[requirements-review.md](docs/requirements-review.md) for the trade-offs and
hard limits.

## Repository map

```text
src/btc_sentinel/       Python domain, data, analysis, news-risk, and signal engines
migrations/             SQLite/D1-compatible migrations
tests/                  Python domain and migration tests
worker/                 Telegram command Worker and TypeScript tests
scripts/                Repository safety checks
docs/                   Architecture, schema, security, roadmap
.github/workflows/      Python and Worker CI (no live schedule yet)
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
```

The command Worker is checked separately:

```bash
cd worker
npm ci
npm run format:check
npm run typecheck
npm test
```

Deployment stays intentionally disabled until Cloudflare D1 and encrypted
secrets are configured. The Worker currently supports `/start`, `/help`,
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
