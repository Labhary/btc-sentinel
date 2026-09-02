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
7. **Lifecycle and persistence — complete**
   Restart-safe one-minute replay, conservative ambiguous-candle handling,
   durable fixed/managed tracks.
8. **Position management — complete**
   Strict versioned decisions, next-candle effect, optional partial accounting,
   fixed baseline preserved.
9. **Statistics — complete**
   Recalculate on each managed close and update when a fixed virtual track later
   resolves.
10. **Reports — complete**
    Monthly, weekly, daily, news risk, active/pending status.
11. **Backtesting — complete**
    Conservative fixed/managed replay, regime coverage, costs, purged
    walk-forward evaluation, sensitivity, and stability gates. The framework is
    complete; a representative exhaustive historical run is still required
    before any performance claim.
12. **Free deployment — in progress, inactive**
    D1 runtime-boundary migration, signed bootstrap/health API, idempotent
    cron-to-workflow dispatch, and independent activation gates are implemented.
    The end-to-end production orchestrator, preview provisioning, encrypted
    secret setup, webhook registration, and activation remain incomplete.
13. **Paper observation**
    Run without money, preserve every result, change rules only through a new
    version backed by evidence.

Machine learning is explicitly outside the first usable baseline. It can be
added only after the deterministic system has enough clean out-of-sample data.
