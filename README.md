# BTC Sentinel

BTC Sentinel is a BTC/USDT-only market-analysis and paper-trading alert bot. It
is being built as a deterministic, auditable system: market levels come from
market data and rules, not from a language model.

> **Current status:** Phase 1 (architecture, domain model, persistence schema,
> and security baseline) is implemented and tested. The project does **not** yet
> send signals and must not be treated as a deployable trading tool.

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

See [architecture.md](docs/architecture.md) and
[requirements-review.md](docs/requirements-review.md) for the trade-offs and
hard limits.

## Repository map

```text
src/btc_sentinel/       Domain, configuration, security, persistence
migrations/             SQLite/D1-compatible migrations
tests/                  Automated Phase 1 tests
scripts/                Repository safety checks
docs/                   Architecture, schema, security, roadmap
.github/workflows/      Continuous integration (no live schedule yet)
```

## Local validation

Python 3.12 is required.

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
pytest
python scripts/check_no_secrets.py
```

The tests also use only Python's standard `unittest` assertions, so the core
suite can be run without downloading pytest:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

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
