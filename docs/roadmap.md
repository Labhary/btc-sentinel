# Build roadmap

Each phase must pass tests before the next phase is enabled. A later phase may
add files without activating production schedules.

1. **Architecture and security — complete**
   Domain invariants, durable schema, immutable audit trail, secure config.
2. **Telegram foundation — complete**
   Command Worker, webhook authentication, owner authorization, message
   templates, fake transport, pause/resume.
3. **Market data — complete**
   Spot/futures candles, funding, open interest, taker data, validation,
   retries, rate limits, stale detection.
4. **Multi-timeframe analysis — complete**
   Indicators, structure, zones, regime, evidence-group scoring.
5. **News and macro risk — complete**
   Official feeds, scheduled releases, GDELT, deduplication, source confidence,
   no-trade windows.
6. **Signal engine — complete**
   Conservative fill model, stop/targets, net R, expiry, selectivity, cooldown.
7. **Lifecycle and persistence — next**
   One-minute replay, ambiguous-candle handling, fixed/managed tracks.
8. **Position management**
   Strict versioned actions, partial accounting, no hindsight.
9. **Statistics**
   Recalculate on each managed close and update when a fixed virtual track later
   resolves.
10. **Reports**
    Monthly, weekly, daily, news risk, active/pending status.
11. **Backtesting**
    Regime coverage, costs, walk-forward evaluation, sensitivity, stability.
12. **Free deployment**
    D1 migration, Worker deployment, encrypted secrets, cron-to-workflow
    dispatch, health.
13. **Paper observation**
    Run without money, preserve every result, change rules only through a new
    version backed by evidence.

Machine learning is explicitly outside the first usable baseline. It can be
added only after the deterministic system has enough clean out-of-sample data.
