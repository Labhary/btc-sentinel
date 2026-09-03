import type { RepositoryCommand } from "../contracts";

const SIGNAL_ID = /^BTC-\d{8}-\d{3,}$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/;
const DECIMAL = /^-?(?:0|[1-9]\d*)(?:\.\d+)?$/;
const KEY = /^[A-Za-z0-9_.:+-]{1,256}$/;

function object(value: unknown, keys: string[]): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error("shape");
  const item = value as Record<string, unknown>;
  if (Object.keys(item).sort().join("|") !== [...keys].sort().join("|")) throw new Error("keys");
  return item;
}

function text(value: unknown, pattern: RegExp, maximum = 5000): string {
  if (typeof value !== "string" || value.length > maximum || !pattern.test(value)) {
    throw new Error("text");
  }
  return value;
}

function plain(value: unknown, maximum = 5000): string {
  if (typeof value !== "string" || value.length < 1 || value.length > maximum)
    throw new Error("text");
  return value;
}

function choice(value: unknown, values: string[]): string {
  if (typeof value !== "string" || !values.includes(value)) throw new Error("choice");
  return value;
}

function integer(value: unknown, minimum: number, maximum: number): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum || (value as number) > maximum) {
    throw new Error("integer");
  }
  return value as number;
}

function json(value: unknown): string {
  return JSON.stringify(value);
}

function assertOne(database: D1Database): D1PreparedStatement[] {
  return [
    database.prepare("INSERT INTO runtime_assertions(changed_rows) VALUES (changes())"),
    database.prepare("DELETE FROM runtime_assertions"),
  ];
}

async function duplicate(database: D1Database, key: string, operation: string): Promise<boolean> {
  const row = await database
    .prepare("SELECT operation FROM runtime_mutations WHERE dedupe_key = ?")
    .bind(key)
    .first<{ operation: string }>();
  if (!row) return false;
  if (row.operation !== operation) throw new Error("dedupe collision");
  return true;
}

function receipt(database: D1Database, key: string, operation: string, at: string) {
  return database
    .prepare("INSERT INTO runtime_mutations(dedupe_key, operation, applied_at) VALUES (?, ?, ?)")
    .bind(key, operation, at);
}

async function allocateSignalId(database: D1Database, args: Record<string, unknown>) {
  const businessDate = text(args.business_date, DATE, 10);
  const row = await database
    .prepare(
      `INSERT INTO signal_id_counters(business_date, last_sequence)
       VALUES (?, 1)
       ON CONFLICT(business_date) DO UPDATE SET last_sequence = last_sequence + 1
       RETURNING last_sequence`,
    )
    .bind(businessDate)
    .first<{ last_sequence: number }>();
  if (!row || !Number.isSafeInteger(row.last_sequence)) throw new Error("allocation");
  return `BTC-${businessDate.replaceAll("-", "")}-${String(row.last_sequence).padStart(3, "0")}`;
}

async function createSignal(
  database: D1Database,
  args: Record<string, unknown>,
  ownerChatId: string,
) {
  const signal = object(args.signal, [
    "biases",
    "created_at",
    "data_timestamp",
    "entry_high",
    "entry_low",
    "estimated_cost_rate",
    "expiration_condition",
    "expires_at",
    "invalidation_condition",
    "minimum_planned_rr",
    "original_stop",
    "reasons",
    "recommended_risk_percent",
    "regime",
    "risks",
    "row_version",
    "setup_score",
    "side",
    "signal_id",
    "status",
    "strategy_version",
    "symbol",
    "targets",
  ]);
  const biases = object(signal.biases, [
    "daily",
    "fifteen_minute",
    "four_hour",
    "monthly",
    "one_hour",
    "weekly",
  ]);
  const targets = signal.targets;
  if (!Array.isArray(targets) || targets.length < 2 || targets.length > 3)
    throw new Error("targets");
  const signalId = text(signal.signal_id, SIGNAL_ID, 64);
  const createdAt = text(signal.created_at, ISO, 30);
  const notification = object(args.notification, [
    "created_at",
    "dedupe_key",
    "message_type",
    "signal_id",
    "text",
  ]);
  const notificationKey = text(notification.dedupe_key, KEY, 256);
  if (
    choice(notification.message_type, ["SIGNAL"]) !== "SIGNAL" ||
    text(notification.signal_id, SIGNAL_ID, 64) !== signalId ||
    text(notification.created_at, ISO, 30) !== createdAt
  ) {
    throw new Error("notification");
  }
  const notificationText = plain(notification.text, 4096);
  const bias = (name: string) =>
    choice(biases[name], ["BULLISH", "BEARISH", "NEUTRAL", "UNCERTAIN"]);
  const statements: D1PreparedStatement[] = [
    database
      .prepare(
        `INSERT INTO signals(
           signal_id, symbol, side, lifecycle_status, setup_score, regime,
           monthly_bias, weekly_bias, daily_bias, four_hour_bias, one_hour_bias,
           fifteen_minute_bias, created_at, data_timestamp, expires_at, entry_low,
           entry_high, original_stop, estimated_cost_rate, minimum_planned_rr,
           invalidation_condition, expiration_condition, recommended_risk_percent,
           reasons_json, risks_json, strategy_version, updated_at, row_version
         ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      )
      .bind(
        signalId,
        choice(signal.symbol, ["BTCUSDT"]),
        choice(signal.side, ["LONG", "SHORT"]),
        choice(signal.status, ["PENDING"]),
        integer(signal.setup_score, 0, 100),
        choice(signal.regime, [
          "BULLISH_TREND",
          "BEARISH_TREND",
          "RANGE",
          "TRANSITION",
          "ABNORMALLY_VOLATILE",
          "NO_RELIABLE_REGIME",
        ]),
        bias("monthly"),
        bias("weekly"),
        bias("daily"),
        bias("four_hour"),
        bias("one_hour"),
        bias("fifteen_minute"),
        createdAt,
        text(signal.data_timestamp, ISO, 30),
        text(signal.expires_at, ISO, 30),
        text(signal.entry_low, DECIMAL, 64),
        text(signal.entry_high, DECIMAL, 64),
        text(signal.original_stop, DECIMAL, 64),
        text(signal.estimated_cost_rate, DECIMAL, 64),
        text(signal.minimum_planned_rr, DECIMAL, 64),
        plain(signal.invalidation_condition),
        plain(signal.expiration_condition),
        text(signal.recommended_risk_percent, DECIMAL, 64),
        json(signal.reasons),
        json(signal.risks),
        plain(signal.strategy_version, 128),
        createdAt,
        integer(signal.row_version, 1, 1),
      ),
  ];
  for (const [index, raw] of targets.entries()) {
    const target = object(raw, ["ordinal", "planned_r", "price"]);
    const ordinal = integer(target.ordinal, 1, 3);
    if (ordinal !== index + 1) throw new Error("target order");
    statements.push(
      database
        .prepare(
          "INSERT INTO signal_targets(signal_id, ordinal, price, planned_r) VALUES (?, ?, ?, ?)",
        )
        .bind(
          signalId,
          ordinal,
          text(target.price, DECIMAL, 64),
          text(target.planned_r, DECIMAL, 64),
        ),
    );
  }
  statements.push(
    database
      .prepare(
        `INSERT INTO trade_events(
           event_id, signal_id, variant, event_type, occurred_at, price,
           payload_json, dedupe_key, created_at
         ) VALUES (?, ?, NULL, 'SIGNAL_CREATED', ?, NULL, ?, ?, ?)`,
      )
      .bind(
        crypto.randomUUID(),
        signalId,
        createdAt,
        json({ strategy_version: signal.strategy_version }),
        `signal:${signalId}:created`,
        createdAt,
      ),
  );
  statements.push(
    database
      .prepare(
        `INSERT INTO outbox(
           outbox_id, signal_id, message_type, payload_json, delivery_status,
           dedupe_key, attempt_count, available_at, created_at, updated_at
         ) VALUES (?, ?, 'SIGNAL', ?, 'PENDING', ?, 0, ?, ?, ?)`,
      )
      .bind(
        `runtime-${notificationKey}`,
        signalId,
        json({ chat_id: ownerChatId, text: notificationText }),
        notificationKey,
        createdAt,
        createdAt,
        createdAt,
      ),
  );
  try {
    await database.batch(statements);
  } catch (error) {
    const existing = await database
      .prepare(
        `SELECT 1 AS found FROM signals s JOIN outbox o ON o.signal_id = s.signal_id
         WHERE s.signal_id = ? AND o.dedupe_key = ? AND o.message_type = 'SIGNAL'`,
      )
      .bind(signalId, notificationKey)
      .first();
    if (!existing) throw error;
  }
  return null;
}

async function lifecycleSignal(database: D1Database, signalId: string) {
  const row = await database
    .prepare(
      `SELECT s.*, t.fill_price, t.activated_at
       FROM signals s LEFT JOIN trades t ON t.signal_id = s.signal_id
       WHERE s.signal_id = ?`,
    )
    .bind(signalId)
    .first<Record<string, unknown>>();
  if (!row) return null;
  const [targets, tracks] = await Promise.all([
    database
      .prepare("SELECT ordinal, price FROM signal_targets WHERE signal_id = ? ORDER BY ordinal")
      .bind(signalId)
      .all<Record<string, unknown>>(),
    database
      .prepare(
        `SELECT variant, current_stop, remaining_fraction, realized_r
         FROM trade_tracks WHERE signal_id = ? AND track_status = 'ACTIVE' ORDER BY variant`,
      )
      .bind(signalId)
      .all<Record<string, unknown>>(),
  ]);
  return {
    signal_id: signalId,
    status: row.lifecycle_status,
    side: row.side,
    created_at: row.created_at,
    expires_at: row.expires_at,
    entry_low: row.entry_low,
    entry_high: row.entry_high,
    original_stop: row.original_stop,
    estimated_cost_rate: row.estimated_cost_rate,
    recommended_risk_percent: row.recommended_risk_percent,
    fill_price: row.fill_price ?? null,
    activated_at: row.activated_at ?? null,
    targets: targets.results,
    active_tracks: tracks.results,
  };
}

async function activateSignal(database: D1Database, args: Record<string, unknown>) {
  const signalId = text(args.signal_id, SIGNAL_ID, 64);
  const dedupeKey = text(args.dedupe_key, KEY, 256);
  if (await duplicate(database, dedupeKey, "activate_signal")) return null;
  const at = text(args.occurred_at, ISO, 30);
  const fill = text(args.fill_price, DECIMAL, 64);
  const targets = await database
    .prepare(
      "SELECT ordinal, price, planned_r FROM signal_targets WHERE signal_id = ? ORDER BY ordinal",
    )
    .bind(signalId)
    .all();
  try {
    await database.batch([
      receipt(database, dedupeKey, "activate_signal", at),
      database
        .prepare(
          `UPDATE signals SET lifecycle_status = 'ACTIVE', updated_at = ?, row_version = row_version + 1
           WHERE signal_id = ? AND lifecycle_status = 'PENDING'`,
        )
        .bind(at, signalId),
      ...assertOne(database),
      database
        .prepare(
          `INSERT INTO trades(
             signal_id, activated_at, fill_price, original_entry_low, original_entry_high,
             original_stop, original_targets_json, strategy_version, activation_event_key
           ) SELECT signal_id, ?, ?, entry_low, entry_high, original_stop, ?, strategy_version, ?
             FROM signals WHERE signal_id = ? AND lifecycle_status = 'ACTIVE'`,
        )
        .bind(at, fill, json(targets.results), dedupeKey, signalId),
      database
        .prepare(
          `INSERT INTO trade_tracks(signal_id, variant, track_status, current_stop,
             remaining_fraction, realized_r, updated_at, row_version)
           SELECT signal_id, 'FIXED', 'ACTIVE', original_stop, '1', '0', ?, 1 FROM signals WHERE signal_id = ?
           UNION ALL
           SELECT signal_id, 'MANAGED', 'ACTIVE', original_stop, '1', '0', ?, 1 FROM signals WHERE signal_id = ?`,
        )
        .bind(at, signalId, at, signalId),
      database
        .prepare(
          `INSERT INTO trade_events(event_id, signal_id, variant, event_type, occurred_at,
             price, payload_json, dedupe_key, created_at)
           VALUES (?, ?, NULL, 'ENTRY_ACTIVATED', ?, ?, ?, ?, ?)`,
        )
        .bind(
          crypto.randomUUID(),
          signalId,
          at,
          fill,
          json({ fill_policy: "conservative-v1" }),
          dedupeKey,
          at,
        ),
    ]);
  } catch (error) {
    if (!(await duplicate(database, dedupeKey, "activate_signal"))) throw error;
  }
  return null;
}

async function transitionPending(database: D1Database, args: Record<string, unknown>) {
  const signalId = text(args.signal_id, SIGNAL_ID, 64);
  const status = choice(args.status, ["EXPIRED", "CANCELLED"]);
  const dedupeKey = text(args.dedupe_key, KEY, 256);
  if (await duplicate(database, dedupeKey, "transition_pending")) return null;
  const at = text(args.occurred_at, ISO, 30);
  const event = status === "EXPIRED" ? "ENTRY_EXPIRED" : "SIGNAL_CANCELLED";
  try {
    await database.batch([
      receipt(database, dedupeKey, "transition_pending", at),
      database
        .prepare(
          `UPDATE signals SET lifecycle_status = ?, updated_at = ?, row_version = row_version + 1
           WHERE signal_id = ? AND lifecycle_status = 'PENDING'`,
        )
        .bind(status, at, signalId),
      ...assertOne(database),
      database
        .prepare(
          `INSERT INTO trade_events(event_id, signal_id, variant, event_type, occurred_at,
             price, payload_json, dedupe_key, created_at)
           VALUES (?, ?, NULL, ?, ?, NULL, '{}', ?, ?)`,
        )
        .bind(crypto.randomUUID(), signalId, event, at, dedupeKey, at),
    ]);
  } catch (error) {
    if (!(await duplicate(database, dedupeKey, "transition_pending"))) throw error;
  }
  return null;
}

async function closeTrack(database: D1Database, args: Record<string, unknown>) {
  const signalId = text(args.signal_id, SIGNAL_ID, 64);
  const variant = choice(args.variant, ["FIXED", "MANAGED"]);
  const dedupeKey = text(args.dedupe_key, KEY, 256);
  if (await duplicate(database, dedupeKey, "close_track")) return null;
  const at = text(args.occurred_at, ISO, 30);
  const result = choice(args.result, ["WIN", "LOSS", "BREAK_EVEN", "EARLY_EXIT"]);
  const closeEvent = choice(args.close_event, [
    "TP1_HIT",
    "TP2_HIT",
    "TP3_HIT",
    "STOP_LOSS_HIT",
    "BREAK_EVEN",
    "EARLY_EXIT",
  ]);
  const resultR = text(args.result_r, DECIMAL, 64);
  const resultPercent = text(args.result_percent, DECIMAL, 64);
  const price = text(args.price, DECIMAL, 64);
  const reason = plain(args.close_reason);
  const details = object(args.details, Object.keys(args.details as object));
  const statistics = object(args.statistics_payload, [
    "calculated_at",
    "comparison",
    "fixed",
    "managed",
    "rate_policy",
    "strategy_counts",
  ]);
  const statements: D1PreparedStatement[] = [
    receipt(database, dedupeKey, "close_track", at),
    database
      .prepare(
        `UPDATE trade_tracks SET track_status = 'CLOSED', realized_r = ?, closed_at = ?,
           updated_at = ?, row_version = row_version + 1
         WHERE signal_id = ? AND variant = ? AND track_status = 'ACTIVE'`,
      )
      .bind(resultR, at, at, signalId, variant),
    ...assertOne(database),
    database
      .prepare(
        `INSERT INTO outcomes(outcome_id, signal_id, variant, result, result_r, result_percent,
           close_reason, closed_at, details_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      )
      .bind(
        crypto.randomUUID(),
        signalId,
        variant,
        result,
        resultR,
        resultPercent,
        reason,
        at,
        json(details),
      ),
    database
      .prepare(
        `INSERT INTO trade_events(event_id, signal_id, variant, event_type, occurred_at,
           price, payload_json, dedupe_key, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      )
      .bind(
        crypto.randomUUID(),
        signalId,
        variant,
        closeEvent,
        at,
        price,
        json({ result, result_r: resultR }),
        `${dedupeKey}:reason`,
        at,
      ),
    database
      .prepare(
        `INSERT INTO trade_events(event_id, signal_id, variant, event_type, occurred_at,
           price, payload_json, dedupe_key, created_at) VALUES (?, ?, ?, 'CLOSED', ?, ?, ?, ?, ?)`,
      )
      .bind(
        crypto.randomUUID(),
        signalId,
        variant,
        at,
        price,
        json({ close_reason: reason }),
        `${dedupeKey}:closed`,
        at,
      ),
  ];
  if (variant === "MANAGED") {
    statements.push(
      database
        .prepare(
          `UPDATE signals SET lifecycle_status = 'CLOSED', updated_at = ?, row_version = row_version + 1
           WHERE signal_id = ? AND lifecycle_status = 'ACTIVE'`,
        )
        .bind(at, signalId),
      ...assertOne(database),
    );
  }
  statements.push(
    database
      .prepare(
        `INSERT INTO statistics_snapshots(snapshot_id, triggering_signal_id,
           triggering_variant, calculated_at, strategy_version, payload_json, dedupe_key)
         VALUES (?, ?, ?, ?, 'statistics-v0.9.0', ?, ?)`,
      )
      .bind(
        crypto.randomUUID(),
        signalId,
        variant,
        at,
        json(statistics),
        `statistics:${signalId}:${variant}:statistics-v0.9.0`,
      ),
  );
  try {
    await database.batch(statements);
  } catch (error) {
    if (!(await duplicate(database, dedupeKey, "close_track"))) throw error;
  }
  return null;
}

async function advanceCheckpoint(database: D1Database, args: Record<string, unknown>) {
  const key = text(args.checkpoint_key, KEY, 256);
  const at = text(args.processed_at, ISO, 30);
  const payload = object(args.payload, Object.keys(args.payload as object));
  await database
    .prepare(
      `INSERT INTO processing_checkpoints(checkpoint_key, last_processed_at, source_cursor,
         payload_json, updated_at, row_version) VALUES (?, ?, NULL, ?, ?, 1)
       ON CONFLICT(checkpoint_key) DO UPDATE SET
         last_processed_at = excluded.last_processed_at,
         payload_json = excluded.payload_json,
         updated_at = excluded.updated_at,
         row_version = processing_checkpoints.row_version + 1
       WHERE excluded.last_processed_at > processing_checkpoints.last_processed_at`,
    )
    .bind(key, at, json(payload), at)
    .run();
  return null;
}

async function applyManagement(database: D1Database, args: Record<string, unknown>) {
  const decision = object(args.decision, [
    "action",
    "changes_managed_result",
    "current_price",
    "decided_at",
    "dedupe_key",
    "evidence",
    "realized_r_after",
    "reason",
    "remaining_fraction_after",
    "signal_id",
    "strategy_version",
    "unrealized_percent",
    "unrealized_r",
    "updated_stop",
  ]);
  const signalId = text(decision.signal_id, SIGNAL_ID, 64);
  const dedupeKey = text(decision.dedupe_key, KEY, 256);
  if (await duplicate(database, dedupeKey, "apply_management_decision")) return null;
  const at = text(decision.decided_at, ISO, 30);
  const changesResult = decision.changes_managed_result;
  if (typeof changesResult !== "boolean") throw new Error("changes flag");
  const nullableDecimal = (value: unknown) => (value === null ? null : text(value, DECIMAL, 64));
  const stop = nullableDecimal(decision.updated_stop);
  const remaining = nullableDecimal(decision.remaining_fraction_after);
  const realized = nullableDecimal(decision.realized_r_after);
  const statements: D1PreparedStatement[] = [
    receipt(database, dedupeKey, "apply_management_decision", at),
    database
      .prepare(
        `UPDATE trade_tracks SET updated_at = updated_at
         WHERE signal_id = ? AND variant = 'MANAGED' AND track_status = 'ACTIVE'`,
      )
      .bind(signalId),
    ...assertOne(database),
  ];
  if (changesResult) {
    statements.push(
      database
        .prepare(
          `UPDATE trade_tracks SET current_stop = COALESCE(?, current_stop),
             remaining_fraction = COALESCE(?, remaining_fraction),
             realized_r = COALESCE(?, realized_r), updated_at = ?, row_version = row_version + 1
           WHERE signal_id = ? AND variant = 'MANAGED' AND track_status = 'ACTIVE'`,
        )
        .bind(stop, remaining, realized, at, signalId),
      ...assertOne(database),
    );
  }
  statements.push(
    database
      .prepare(
        `INSERT INTO management_decisions(decision_id, signal_id, decided_at, action,
           current_price, unrealized_percent, unrealized_r, reason, updated_stop,
           updated_target, changes_managed_result, strategy_version, evidence_json, dedupe_key)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)`,
      )
      .bind(
        crypto.randomUUID(),
        signalId,
        at,
        choice(decision.action, [
          "HOLD",
          "MOVE_STOP_TO_BREAK_EVEN",
          "REDUCE_POSITION",
          "TAKE_PARTIAL_PROFIT",
          "TRAIL_STOP",
          "CLOSE_POSITION_NOW",
          "CANCEL_PENDING_ENTRY",
        ]),
        text(decision.current_price, DECIMAL, 64),
        text(decision.unrealized_percent, DECIMAL, 64),
        text(decision.unrealized_r, DECIMAL, 64),
        plain(decision.reason),
        stop,
        changesResult ? 1 : 0,
        plain(decision.strategy_version, 128),
        json(object(decision.evidence, Object.keys(decision.evidence as object))),
        dedupeKey,
      ),
    database
      .prepare(
        `INSERT INTO trade_events(event_id, signal_id, variant, event_type, occurred_at,
           price, payload_json, dedupe_key, created_at)
         VALUES (?, ?, 'MANAGED', 'MANAGEMENT_DECISION', ?, ?, ?, ?, ?)`,
      )
      .bind(
        crypto.randomUUID(),
        signalId,
        at,
        text(decision.current_price, DECIMAL, 64),
        json({
          action: decision.action,
          changes_managed_result: changesResult,
          strategy_version: decision.strategy_version,
        }),
        `${dedupeKey}:event`,
        at,
      ),
  );
  try {
    await database.batch(statements);
  } catch (error) {
    if (!(await duplicate(database, dedupeKey, "apply_management_decision"))) throw error;
  }
  return null;
}

async function outcomeSamples(database: D1Database, args: Record<string, unknown>) {
  const start = args.start_at === null ? null : text(args.start_at, ISO, 30);
  const end = args.end_at === null ? null : text(args.end_at, ISO, 30);
  const cursorAt = args.cursor_closed_at === null ? null : text(args.cursor_closed_at, ISO, 30);
  const cursorId = args.cursor_id === null ? null : plain(args.cursor_id, 128);
  if ((cursorAt === null) !== (cursorId === null)) throw new Error("cursor");
  const rows = await database
    .prepare(
      `SELECT o.outcome_id, o.signal_id, o.variant, o.result, o.result_r, o.closed_at,
         s.strategy_version
       FROM outcomes o JOIN signals s ON s.signal_id = o.signal_id
       WHERE (? IS NULL OR o.closed_at >= ?) AND (? IS NULL OR o.closed_at < ?)
         AND (? IS NULL OR o.closed_at > ? OR (o.closed_at = ? AND o.outcome_id > ?))
       ORDER BY o.closed_at, o.outcome_id LIMIT 101`,
    )
    .bind(start, start, end, end, cursorAt, cursorAt, cursorAt, cursorId)
    .all<Record<string, unknown>>();
  const page = rows.results.slice(0, 100);
  const last = page.at(-1);
  return {
    items: page.map(({ outcome_id: _outcomeId, ...item }) => item),
    next_cursor:
      rows.results.length <= 100 || !last
        ? null
        : { closed_at: last.closed_at, outcome_id: last.outcome_id },
  };
}

async function reportSignals(database: D1Database, args: Record<string, unknown>) {
  const status = choice(args.status, ["PENDING", "ACTIVE"]);
  const rows = await database
    .prepare(
      `SELECT s.signal_id, s.lifecycle_status AS status, s.side, s.regime, s.setup_score,
         s.created_at, s.expires_at, s.entry_low, s.entry_high, s.original_stop,
         s.strategy_version, t.fill_price, t.activated_at,
         mt.current_stop AS managed_stop,
         CASE WHEN ft.track_status = 'ACTIVE' THEN 1 ELSE 0 END AS fixed_track_active,
         CASE WHEN mt.track_status = 'ACTIVE' THEN 1 ELSE 0 END AS managed_track_active
       FROM signals s
       LEFT JOIN trades t ON t.signal_id = s.signal_id
       LEFT JOIN trade_tracks ft ON ft.signal_id = s.signal_id AND ft.variant = 'FIXED'
       LEFT JOIN trade_tracks mt ON mt.signal_id = s.signal_id AND mt.variant = 'MANAGED'
       WHERE s.lifecycle_status = ? ORDER BY s.created_at, s.signal_id LIMIT 101`,
    )
    .bind(status)
    .all<Record<string, unknown>>();
  if (rows.results.length > 100) throw new Error("report limit");
  const result = [];
  for (const row of rows.results) {
    const targets = await database
      .prepare("SELECT ordinal, price FROM signal_targets WHERE signal_id = ? ORDER BY ordinal")
      .bind(row.signal_id)
      .all();
    result.push({ ...row, targets: targets.results });
  }
  return result;
}

export async function executeRuntimeRepository(
  database: D1Database,
  command: RepositoryCommand,
  ownerChatId: string,
): Promise<unknown> {
  const args = command.arguments;
  switch (command.operation) {
    case "allocate_signal_id":
      return allocateSignalId(database, args);
    case "create_signal":
      return createSignal(database, args, ownerChatId);
    case "get_signal_status": {
      const row = await database
        .prepare("SELECT lifecycle_status FROM signals WHERE signal_id = ?")
        .bind(text(args.signal_id, SIGNAL_ID, 64))
        .first<{ lifecycle_status: string }>();
      return row?.lifecycle_status ?? null;
    }
    case "get_signal_strategy": {
      const row = await database
        .prepare("SELECT strategy_version FROM signals WHERE signal_id = ?")
        .bind(text(args.signal_id, SIGNAL_ID, 64))
        .first<{ strategy_version: string }>();
      return row?.strategy_version ?? null;
    }
    case "get_lifecycle_signal":
      return lifecycleSignal(database, text(args.signal_id, SIGNAL_ID, 64));
    case "activate_signal":
      return activateSignal(database, args);
    case "transition_pending":
      return transitionPending(database, args);
    case "close_track":
      return closeTrack(database, args);
    case "get_track_status": {
      const row = await database
        .prepare("SELECT track_status FROM trade_tracks WHERE signal_id = ? AND variant = ?")
        .bind(text(args.signal_id, SIGNAL_ID, 64), choice(args.variant, ["FIXED", "MANAGED"]))
        .first<{ track_status: string }>();
      return row?.track_status ?? null;
    }
    case "get_checkpoint": {
      const row = await database
        .prepare("SELECT last_processed_at FROM processing_checkpoints WHERE checkpoint_key = ?")
        .bind(text(args.checkpoint_key, KEY, 256))
        .first<{ last_processed_at: string }>();
      return row?.last_processed_at ?? null;
    }
    case "advance_checkpoint":
      return advanceCheckpoint(database, args);
    case "apply_management_decision":
      return applyManagement(database, args);
    case "management_decision_exists": {
      const row = await database
        .prepare("SELECT 1 AS found FROM management_decisions WHERE dedupe_key = ?")
        .bind(text(args.dedupe_key, KEY, 256))
        .first();
      return row !== null;
    }
    case "get_latest_statistics_snapshot": {
      const row = await database
        .prepare(
          `SELECT snapshot_id, triggering_signal_id, triggering_variant, calculated_at,
             strategy_version, payload_json, dedupe_key
           FROM statistics_snapshots ORDER BY calculated_at DESC, rowid DESC LIMIT 1`,
        )
        .first<Record<string, unknown>>();
      if (!row) return null;
      const { payload_json: payloadJson, ...metadata } = row;
      return { ...metadata, payload: JSON.parse(String(payloadJson)) };
    }
    case "list_outcome_samples":
      return outcomeSamples(database, args);
    case "list_report_signals":
      return reportSignals(database, args);
    default:
      throw new Error("operation");
  }
}
