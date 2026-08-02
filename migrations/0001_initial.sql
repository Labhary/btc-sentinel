PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_migrations(version, applied_at)
VALUES (1, '2026-08-02T00:00:00Z');

CREATE TABLE IF NOT EXISTS signal_id_counters (
    business_date TEXT PRIMARY KEY,
    last_sequence INTEGER NOT NULL CHECK (last_sequence >= 1)
);

CREATE TABLE IF NOT EXISTS signals (
    signal_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL CHECK (symbol = 'BTCUSDT'),
    side TEXT NOT NULL CHECK (side IN ('LONG', 'SHORT')),
    lifecycle_status TEXT NOT NULL CHECK (
        lifecycle_status IN ('PENDING', 'ACTIVE', 'EXPIRED', 'CANCELLED', 'CLOSED')
    ),
    setup_score INTEGER NOT NULL CHECK (setup_score BETWEEN 0 AND 100),
    regime TEXT NOT NULL CHECK (
        regime IN (
            'BULLISH_TREND', 'BEARISH_TREND', 'RANGE', 'TRANSITION',
            'ABNORMALLY_VOLATILE', 'NO_RELIABLE_REGIME'
        )
    ),
    monthly_bias TEXT NOT NULL CHECK (monthly_bias IN ('BULLISH','BEARISH','NEUTRAL','UNCERTAIN')),
    weekly_bias TEXT NOT NULL CHECK (weekly_bias IN ('BULLISH','BEARISH','NEUTRAL','UNCERTAIN')),
    daily_bias TEXT NOT NULL CHECK (daily_bias IN ('BULLISH','BEARISH','NEUTRAL','UNCERTAIN')),
    four_hour_bias TEXT NOT NULL CHECK (four_hour_bias IN ('BULLISH','BEARISH','NEUTRAL','UNCERTAIN')),
    one_hour_bias TEXT NOT NULL CHECK (one_hour_bias IN ('BULLISH','BEARISH','NEUTRAL','UNCERTAIN')),
    fifteen_minute_bias TEXT NOT NULL CHECK (fifteen_minute_bias IN ('BULLISH','BEARISH','NEUTRAL','UNCERTAIN')),
    created_at TEXT NOT NULL,
    data_timestamp TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    entry_low TEXT NOT NULL,
    entry_high TEXT NOT NULL,
    original_stop TEXT NOT NULL,
    estimated_cost_rate TEXT NOT NULL,
    minimum_planned_rr TEXT NOT NULL,
    invalidation_condition TEXT NOT NULL,
    expiration_condition TEXT NOT NULL,
    recommended_risk_percent TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    risks_json TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_one_active_btc_signal
ON signals(symbol)
WHERE lifecycle_status = 'ACTIVE';

CREATE INDEX IF NOT EXISTS ix_signals_status_created
ON signals(lifecycle_status, created_at DESC);

CREATE TABLE IF NOT EXISTS signal_targets (
    signal_id TEXT NOT NULL REFERENCES signals(signal_id),
    ordinal INTEGER NOT NULL CHECK (ordinal BETWEEN 1 AND 3),
    price TEXT NOT NULL,
    planned_r TEXT NOT NULL,
    PRIMARY KEY (signal_id, ordinal)
);

CREATE TABLE IF NOT EXISTS trades (
    signal_id TEXT PRIMARY KEY REFERENCES signals(signal_id),
    activated_at TEXT NOT NULL,
    fill_price TEXT NOT NULL,
    original_entry_low TEXT NOT NULL,
    original_entry_high TEXT NOT NULL,
    original_stop TEXT NOT NULL,
    original_targets_json TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    activation_event_key TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS trade_tracks (
    signal_id TEXT NOT NULL REFERENCES trades(signal_id),
    variant TEXT NOT NULL CHECK (variant IN ('FIXED', 'MANAGED')),
    track_status TEXT NOT NULL CHECK (track_status IN ('ACTIVE', 'CLOSED')),
    current_stop TEXT NOT NULL,
    remaining_fraction TEXT NOT NULL,
    realized_r TEXT NOT NULL DEFAULT '0',
    updated_at TEXT NOT NULL,
    closed_at TEXT,
    row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    PRIMARY KEY (signal_id, variant)
);

CREATE INDEX IF NOT EXISTS ix_tracks_status_variant
ON trade_tracks(track_status, variant);

CREATE TABLE IF NOT EXISTS trade_events (
    event_id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL REFERENCES signals(signal_id),
    variant TEXT CHECK (variant IS NULL OR variant IN ('FIXED', 'MANAGED')),
    event_type TEXT NOT NULL CHECK (
        event_type IN (
            'SIGNAL_CREATED', 'ENTRY_ACTIVATED', 'ENTRY_EXPIRED',
            'SIGNAL_CANCELLED', 'TP1_HIT', 'TP2_HIT', 'TP3_HIT',
            'STOP_LOSS_HIT', 'BREAK_EVEN', 'EARLY_EXIT', 'CLOSED',
            'MANAGEMENT_DECISION'
        )
    ),
    occurred_at TEXT NOT NULL,
    price TEXT,
    payload_json TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_trade_events_signal_time
ON trade_events(signal_id, occurred_at, event_id);

CREATE TABLE IF NOT EXISTS management_decisions (
    decision_id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL REFERENCES trades(signal_id),
    decided_at TEXT NOT NULL,
    action TEXT NOT NULL CHECK (
        action IN (
            'HOLD', 'MOVE_STOP_TO_BREAK_EVEN', 'REDUCE_POSITION',
            'TAKE_PARTIAL_PROFIT', 'TRAIL_STOP', 'CLOSE_POSITION_NOW',
            'CANCEL_PENDING_ENTRY'
        )
    ),
    current_price TEXT NOT NULL,
    unrealized_percent TEXT NOT NULL,
    unrealized_r TEXT NOT NULL,
    reason TEXT NOT NULL,
    updated_stop TEXT,
    updated_target TEXT,
    changes_managed_result INTEGER NOT NULL CHECK (changes_managed_result IN (0, 1)),
    strategy_version TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS outcomes (
    outcome_id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL REFERENCES trades(signal_id),
    variant TEXT NOT NULL CHECK (variant IN ('FIXED', 'MANAGED')),
    result TEXT NOT NULL CHECK (result IN ('WIN', 'LOSS', 'BREAK_EVEN', 'EARLY_EXIT')),
    result_r TEXT NOT NULL,
    result_percent TEXT NOT NULL,
    close_reason TEXT NOT NULL,
    closed_at TEXT NOT NULL,
    details_json TEXT NOT NULL,
    UNIQUE (signal_id, variant)
);

CREATE INDEX IF NOT EXISTS ix_outcomes_closed
ON outcomes(closed_at, variant);

CREATE TABLE IF NOT EXISTS statistics_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    triggering_signal_id TEXT NOT NULL REFERENCES signals(signal_id),
    triggering_variant TEXT NOT NULL CHECK (triggering_variant IN ('FIXED', 'MANAGED')),
    calculated_at TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS news_events (
    news_id TEXT PRIMARY KEY,
    canonical_url TEXT,
    source_name TEXT NOT NULL,
    source_tier INTEGER NOT NULL CHECK (source_tier BETWEEN 1 AND 5),
    title TEXT NOT NULL,
    published_at TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    confirmation_status TEXT NOT NULL CHECK (
        confirmation_status IN ('CONFIRMED', 'UNCONFIRMED', 'DISPUTED')
    ),
    directional_impact TEXT NOT NULL CHECK (
        directional_impact IN ('POSITIVE', 'NEGATIVE', 'NEUTRAL', 'UNCERTAIN')
    ),
    volatility_impact TEXT NOT NULL CHECK (
        volatility_impact IN ('LOW', 'MEDIUM', 'HIGH', 'UNKNOWN')
    ),
    content_fingerprint TEXT NOT NULL UNIQUE,
    raw_reference_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1)
);

CREATE INDEX IF NOT EXISTS ix_news_published
ON news_events(published_at DESC);

CREATE TABLE IF NOT EXISTS scheduled_events (
    event_id TEXT PRIMARY KEY,
    event_name TEXT NOT NULL,
    source_name TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    expected_impact TEXT NOT NULL CHECK (expected_impact IN ('MEDIUM', 'HIGH')),
    no_trade_before_minutes INTEGER NOT NULL CHECK (no_trade_before_minutes >= 0),
    no_trade_after_minutes INTEGER NOT NULL CHECK (no_trade_after_minutes >= 0),
    source_reference TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_scheduled_events_time
ON scheduled_events(scheduled_at);

CREATE TABLE IF NOT EXISTS processing_checkpoints (
    checkpoint_key TEXT PRIMARY KEY,
    last_processed_at TEXT NOT NULL,
    source_cursor TEXT,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1)
);

CREATE TABLE IF NOT EXISTS bot_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1)
);

CREATE TABLE IF NOT EXISTS outbox (
    outbox_id TEXT PRIMARY KEY,
    signal_id TEXT REFERENCES signals(signal_id),
    message_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    delivery_status TEXT NOT NULL CHECK (
        delivery_status IN ('PENDING', 'SENDING', 'SENT', 'FAILED', 'UNKNOWN')
    ),
    dedupe_key TEXT NOT NULL UNIQUE,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    available_at TEXT NOT NULL,
    lease_until TEXT,
    telegram_message_id TEXT,
    last_error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_outbox_delivery
ON outbox(delivery_status, available_at);

CREATE TABLE IF NOT EXISTS health_runs (
    run_id TEXT PRIMARY KEY,
    job_name TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'OK', 'DEGRADED', 'FAILED')),
    data_fresh INTEGER NOT NULL CHECK (data_fresh IN (0, 1)),
    summary_json TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE
);

CREATE TRIGGER IF NOT EXISTS signals_no_delete
BEFORE DELETE ON signals
BEGIN
    SELECT RAISE(ABORT, 'signals are audit records and cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS signals_original_terms_immutable
BEFORE UPDATE ON signals
WHEN
    NEW.symbol IS NOT OLD.symbol OR
    NEW.side IS NOT OLD.side OR
    NEW.setup_score IS NOT OLD.setup_score OR
    NEW.regime IS NOT OLD.regime OR
    NEW.monthly_bias IS NOT OLD.monthly_bias OR
    NEW.weekly_bias IS NOT OLD.weekly_bias OR
    NEW.daily_bias IS NOT OLD.daily_bias OR
    NEW.four_hour_bias IS NOT OLD.four_hour_bias OR
    NEW.one_hour_bias IS NOT OLD.one_hour_bias OR
    NEW.fifteen_minute_bias IS NOT OLD.fifteen_minute_bias OR
    NEW.created_at IS NOT OLD.created_at OR
    NEW.data_timestamp IS NOT OLD.data_timestamp OR
    NEW.expires_at IS NOT OLD.expires_at OR
    NEW.entry_low IS NOT OLD.entry_low OR
    NEW.entry_high IS NOT OLD.entry_high OR
    NEW.original_stop IS NOT OLD.original_stop OR
    NEW.estimated_cost_rate IS NOT OLD.estimated_cost_rate OR
    NEW.minimum_planned_rr IS NOT OLD.minimum_planned_rr OR
    NEW.invalidation_condition IS NOT OLD.invalidation_condition OR
    NEW.expiration_condition IS NOT OLD.expiration_condition OR
    NEW.recommended_risk_percent IS NOT OLD.recommended_risk_percent OR
    NEW.reasons_json IS NOT OLD.reasons_json OR
    NEW.risks_json IS NOT OLD.risks_json OR
    NEW.strategy_version IS NOT OLD.strategy_version
BEGIN
    SELECT RAISE(ABORT, 'original signal terms are immutable');
END;

CREATE TRIGGER IF NOT EXISTS signal_targets_no_update
BEFORE UPDATE ON signal_targets
BEGIN
    SELECT RAISE(ABORT, 'original targets are immutable');
END;

CREATE TRIGGER IF NOT EXISTS signal_targets_no_delete
BEFORE DELETE ON signal_targets
BEGIN
    SELECT RAISE(ABORT, 'original targets cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS trades_no_update
BEFORE UPDATE ON trades
BEGIN
    SELECT RAISE(ABORT, 'trade activation snapshots are immutable');
END;

CREATE TRIGGER IF NOT EXISTS trades_no_delete
BEFORE DELETE ON trades
BEGIN
    SELECT RAISE(ABORT, 'trade activation snapshots cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS trade_events_no_update
BEFORE UPDATE ON trade_events
BEGIN
    SELECT RAISE(ABORT, 'trade events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trade_events_no_delete
BEFORE DELETE ON trade_events
BEGIN
    SELECT RAISE(ABORT, 'trade events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS management_decisions_no_update
BEFORE UPDATE ON management_decisions
BEGIN
    SELECT RAISE(ABORT, 'management decisions are append-only');
END;

CREATE TRIGGER IF NOT EXISTS management_decisions_no_delete
BEFORE DELETE ON management_decisions
BEGIN
    SELECT RAISE(ABORT, 'management decisions are append-only');
END;

CREATE TRIGGER IF NOT EXISTS outcomes_no_update
BEFORE UPDATE ON outcomes
BEGIN
    SELECT RAISE(ABORT, 'outcomes are immutable');
END;

CREATE TRIGGER IF NOT EXISTS outcomes_no_delete
BEFORE DELETE ON outcomes
BEGIN
    SELECT RAISE(ABORT, 'outcomes are immutable');
END;

CREATE TRIGGER IF NOT EXISTS statistics_snapshots_no_update
BEFORE UPDATE ON statistics_snapshots
BEGIN
    SELECT RAISE(ABORT, 'statistics snapshots are append-only');
END;

CREATE TRIGGER IF NOT EXISTS statistics_snapshots_no_delete
BEFORE DELETE ON statistics_snapshots
BEGIN
    SELECT RAISE(ABORT, 'statistics snapshots are append-only');
END;

CREATE TRIGGER IF NOT EXISTS checkpoint_time_monotonic
BEFORE UPDATE ON processing_checkpoints
WHEN NEW.last_processed_at < OLD.last_processed_at
BEGIN
    SELECT RAISE(ABORT, 'checkpoint time cannot move backwards');
END;

CREATE TRIGGER IF NOT EXISTS health_runs_no_delete
BEFORE DELETE ON health_runs
BEGIN
    SELECT RAISE(ABORT, 'health history cannot be deleted');
END;

