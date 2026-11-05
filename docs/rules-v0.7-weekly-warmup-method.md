# Development-only native weekly warm-up method

Status: frozen method before readiness results; no performance evaluation.

## Reason, not a strategy change

The preserved 2021 audit found no complete six-timeframe inputs. Monthly
analysis has native official warm-up, while weekly analysis is rebuilt only
from the one-minute 2021 history and resets at missing complete weeks.
Binance documents native Spot `1w` archives alongside `1mo` archives:
https://github.com/binance/binance-public-data/blob/master/README.md

## Fixed experiment

1. Keep the one-minute market manifest and native monthly manifest unchanged.
2. Acquire only official BTCUSDT Spot `1w` monthly ZIPs for January 2020
   through November 2021 inclusive (23 archives), plus their official
   `.CHECKSUM` files. December 2021 is deliberately excluded because its last
   weekly candle includes January 2022. No 2022–2025 or 2026 input is accessed.
3. Require exact archive filenames, fixed HTTPS host/path, published SHA-256,
   one safe bounded CSV member, 12 fields per row, Monday UTC opens, exact
   seven-day closes, valid OHLC/volumes, and every expected weekly open with no
   missing, duplicated, extra, unordered or truncated rows. Freeze a separate
   checksum-bound manifest. Never overwrite an existing dataset.
4. Import transactionally into the research replay's weekly interval only.
   Check exact agreement with every overlapping complete one-minute-derived
   weekly candle. A discrepancy aborts; no tolerance or source substitution is
   chosen after seeing results. Retain derived candles beyond the native range.
   Never alter one-minute candles, gap ledgers, candidate boundaries, fills,
   or lifecycle accounting. Never expose a candle before its close.
5. Re-run the same full-year 2021 market-only readiness audit. Preserve the
   previous zero-ready report. Report readiness counts, first/last timestamps,
   rejection counts, manifest hashes and overlap-check count. Do not optimise
   a narrower window or inspect signals, trades, scores, risk or profitability.
6. Regardless of the count, performance remains `NOT_RUN`. Zero ready
   boundaries means stop and record the next data limitation. Positive
   readiness permits a separately documented point-in-time risk-validation
   step, not deployment, strategy acceptance, or holdout evaluation.

## Scope and progress

This is a Phase 11 historical-input validation increment at the Phase 12
activation gate. PR #36 only. PR #37, `main`, deployed resources, secrets,
strategy rules and the sealed evidence remain unchanged. Phase 13 has not
started. No exchange writes or production dispatch are authorised.
