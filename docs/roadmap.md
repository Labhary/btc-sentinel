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
    complete. The first Phase 12 validation increment adds streaming, immutable,
    checksum-bound Binance Vision one-minute archive preflight with explicit
    millisecond/microsecond handling. A disk-backed point-in-time index now
    creates complete-only 15m through monthly Spot views and exhaustive 15m
    candidate boundaries without future leakage. The exhaustive signal runner
    now streams fixed/managed lifecycle outcomes, enforces managed activity and
    cooldown, and connects eligible runs to walk-forward evaluation. A strict
    checksum-bound 15-minute news/macro risk timeline now rejects future
    evidence and required-source gaps that do not block. The executable replay
    command now joins immutable market and risk manifests through separate
    fixed/managed verdicts. The public monthly Binance Vision archive builder
    now creates and validates the market manifest without credentials. A
    schema-v2 gap ledger preserves the exact rare official rows whose raw
    close-time field ends early and the absent minutes around official data
    outages; those intervals are excluded and undeclared anomalies still fail. A
    checksum-bound official-source evidence format, deterministic risk timeline
    derivation, and conservative raw official-archive reconstruction are also
    implemented. Present-day Fed/SEC/BLS pages are retained and hashed; records
    cite their raw artifacts, and uncertain BLS availability becomes a required
    blocking gap. The predeclared representative acquisition and historical
    verdict are still required before any performance claim.
12. **Free deployment — in progress, inactive**
    D1 runtime-boundary migration, signed bootstrap/health API, idempotent
    cron-to-workflow dispatch, deterministic orchestration core, typed runtime
    bootstrap, durable notification enqueue, and independent activation gates
    are implemented, together with the typed D1 repository mutation adapter,
    atomic signal/outbox commit, bounded outbox recovery, and migration 4.
    The executable job now wires the public collectors, typed repository,
    orchestrator, notification recovery, and health sink behind the hard gate.
    Representative backtest validation, preview provisioning, encrypted secret
    setup, webhook registration, and activation remain incomplete.
13. **Paper observation**
    Run without money, preserve every result, change rules only through a new
    version backed by evidence.

Machine learning is explicitly outside the first usable baseline. It can be
added only after the deterministic system has enough clean out-of-sample data.
