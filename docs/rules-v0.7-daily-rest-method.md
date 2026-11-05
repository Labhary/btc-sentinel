# Development source method 2: daily minutes and retained REST weeks

Status: frozen before acquisition of the full daily dataset or readiness result.
Research input experiment only; performance remains NOT_RUN.

## Independent source reason

The first exact-overlap experiment remains rejected and is not amended. A
bounded three-way audit of 2021-01-11 found 10 monthly-archive minute rows
disagreeing with both daily archives and current REST minute records. Daily
and REST minute records match for all 1,440 minutes, and their exact totals
match the current daily candle. A first-week check also found current REST
weekly data correcting a discrepancy in the old native-weekly ZIP.

Binance's archive documentation identifies klines as exports of its public
klines endpoint and notes that archives may be revised. Issue #475 reports
related pre-2022 discrepancies; that user report is a lead, not authority.
Our own direct source comparisons justify testing a uniform acquisition
method, not adopting selectively favourable values.

Sources:
- https://github.com/binance/binance-public-data/blob/master/README.md
- https://github.com/binance/binance-spot-api-docs/blob/master/faqs/market_data_only.md
- https://github.com/binance/binance-public-data/issues/475

## Fixed method and hard stops

1. Preserve all original manifests and rejection reports unchanged. Create a
   separately named dataset from all 365 official BTCUSDT Spot daily 1m ZIPs
   dated 2021-01-01 through 2021-12-31 inclusive. No selective row/day patching.
   Verify each publisher CHECKSUM and retain each raw ZIP and checksum.
2. Use unchanged schema-v2 archive inspection and strict loader validation.
   Retain exact anomaly and missing-interval ledgers. Never fill a gap or alter
   a raw candle; never change lifecycle, fills, fees or signal rules.
3. Retain a raw JSON snapshot of exactly 100 native weekly rows from the
   official market-data-only REST klines endpoint, with explicit BTCUSDT/1w
   bounds 2020-01-06 inclusive through 2021-12-06 exclusive. Repeat the same
   bounded request and require identical bytes before publishing its manifest.
   Bind URL, retrieval time and both response hashes into a separate manifest.
4. Enforce every Monday UTC open, exact seven-day close, row types and market
   contract. Never request unbounded/current klines or dates in 2022 onward.
5. Reuse the existing transactional native-weekly importer and its exact
   comparison of every complete overlapping minute-derived week. Any mismatch
   still aborts and rolls back; no tolerance or automatic source fallback.
6. Keep the native monthly warm-up manifest unchanged. Run the same full-year
   2021 market-only readiness audit and preserve its complete result. Compare
   manifests, row counts, anomaly/gap totals and candidate counts with baseline;
   report all changes, not only readiness improvements.
7. Positive readiness is not strategy acceptance or deployment permission.
   Risk validation and signal/performance evaluation remain separate gates.
   If source agreement fails, retain the failure and diagnose it without
   changing this frozen method.

Only PR #36 is changed. No `main`, PR #37, deployed resources, secrets,
sealed 2022–2025 evidence, 2026 holdout or trading permissions are changed.
