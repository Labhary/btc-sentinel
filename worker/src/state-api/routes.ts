import type { AppDependencies } from "../app";
import type { HealthRunInput, RepositoryCommand, SystemNotificationInput } from "../contracts";
import { deliverOnce } from "../outbox/delivery";
import { authorizeStateRequest } from "./auth";

const MAX_STATE_BODY_BYTES = 32 * 1024;
const ID_PATTERN = /^[A-Za-z0-9_.:-]{1,128}$/;
const ISO_UTC_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/;
const REPOSITORY_ARGUMENTS: Record<string, string[]> = {
  allocate_signal_id: ["business_date"],
  create_signal: ["notification", "signal"],
  get_signal_status: ["signal_id"],
  get_signal_strategy: ["signal_id"],
  get_lifecycle_signal: ["signal_id"],
  activate_signal: ["dedupe_key", "fill_price", "occurred_at", "signal_id"],
  transition_pending: ["dedupe_key", "occurred_at", "signal_id", "status"],
  close_track: [
    "close_event",
    "close_reason",
    "dedupe_key",
    "details",
    "occurred_at",
    "price",
    "result",
    "result_percent",
    "result_r",
    "signal_id",
    "statistics_payload",
    "variant",
  ],
  get_track_status: ["signal_id", "variant"],
  get_checkpoint: ["checkpoint_key"],
  advance_checkpoint: ["checkpoint_key", "payload", "processed_at"],
  apply_management_decision: ["decision"],
  management_decision_exists: ["dedupe_key"],
  get_latest_statistics_snapshot: [],
  list_outcome_samples: ["cursor_closed_at", "cursor_id", "end_at", "start_at"],
  list_report_signals: ["status"],
};

function json(body: unknown, status = 200): Response {
  return Response.json(body, {
    status,
    headers: { "cache-control": "no-store", "content-type": "application/json" },
  });
}

function healthRun(value: unknown, now: Date): HealthRunInput | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  const item = value as Record<string, unknown>;
  const keys = Object.keys(item).sort();
  const expected = [
    "data_fresh",
    "dedupe_key",
    "finished_at",
    "job_name",
    "run_id",
    "started_at",
    "status",
    "summary",
  ].sort();
  if (keys.join("|") !== expected.join("|")) {
    return null;
  }
  if (
    typeof item.run_id !== "string" ||
    !ID_PATTERN.test(item.run_id) ||
    typeof item.job_name !== "string" ||
    !ID_PATTERN.test(item.job_name) ||
    typeof item.dedupe_key !== "string" ||
    !ID_PATTERN.test(item.dedupe_key) ||
    typeof item.started_at !== "string" ||
    !ISO_UTC_PATTERN.test(item.started_at) ||
    typeof item.finished_at !== "string" ||
    !ISO_UTC_PATTERN.test(item.finished_at) ||
    !["OK", "DEGRADED", "FAILED"].includes(String(item.status)) ||
    typeof item.data_fresh !== "boolean" ||
    typeof item.summary !== "object" ||
    item.summary === null ||
    Array.isArray(item.summary)
  ) {
    return null;
  }
  const startedAt = Date.parse(item.started_at);
  const finishedAt = Date.parse(item.finished_at);
  if (
    !Number.isFinite(startedAt) ||
    !Number.isFinite(finishedAt) ||
    finishedAt < startedAt ||
    finishedAt > now.getTime() + 5 * 60 * 1000
  ) {
    return null;
  }
  return {
    runId: item.run_id,
    jobName: item.job_name,
    startedAt: item.started_at,
    finishedAt: item.finished_at,
    status: item.status as HealthRunInput["status"],
    dataFresh: item.data_fresh,
    summary: item.summary as Record<string, unknown>,
    dedupeKey: item.dedupe_key,
  };
}

function systemNotification(value: unknown, now: Date): SystemNotificationInput | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  const item = value as Record<string, unknown>;
  const keys = Object.keys(item).sort();
  const expected = ["created_at", "dedupe_key", "message_type", "signal_id", "text"].sort();
  if (keys.join("|") !== expected.join("|")) {
    return null;
  }
  const signalId = item.signal_id;
  if (
    typeof item.message_type !== "string" ||
    !["SIGNAL", "LIFECYCLE", "MANAGEMENT", "REPORT"].includes(item.message_type) ||
    typeof item.text !== "string" ||
    item.text.length < 1 ||
    item.text.length > 4096 ||
    typeof item.dedupe_key !== "string" ||
    !ID_PATTERN.test(item.dedupe_key) ||
    (signalId !== null && (typeof signalId !== "string" || !/^BTC-\d{8}-\d{3,}$/.test(signalId))) ||
    typeof item.created_at !== "string" ||
    !ISO_UTC_PATTERN.test(item.created_at)
  ) {
    return null;
  }
  const createdAt = Date.parse(item.created_at);
  if (!Number.isFinite(createdAt) || createdAt > now.getTime() + 5 * 60 * 1000) {
    return null;
  }
  return {
    messageType: item.message_type as SystemNotificationInput["messageType"],
    text: item.text,
    dedupeKey: item.dedupe_key,
    signalId,
    createdAt: item.created_at,
  };
}

function boundedJson(value: unknown, depth = 0): boolean {
  if (depth > 6) return false;
  if (value === null || typeof value === "boolean") return true;
  if (typeof value === "number") return Number.isSafeInteger(value);
  if (typeof value === "string") return value.length <= 5000;
  if (Array.isArray(value)) {
    return value.length <= 100 && value.every((item) => boundedJson(item, depth + 1));
  }
  if (typeof value !== "object") return false;
  const entries = Object.entries(value as Record<string, unknown>);
  return (
    entries.length <= 64 &&
    entries.every(
      ([key, item]) => /^[a-z][a-z0-9_]{0,63}$/.test(key) && boundedJson(item, depth + 1),
    )
  );
}

function repositoryCommand(value: unknown): RepositoryCommand | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const item = value as Record<string, unknown>;
  if (Object.keys(item).sort().join("|") !== "arguments|operation") return null;
  if (typeof item.operation !== "string") return null;
  const specification = REPOSITORY_ARGUMENTS[item.operation];
  if (!specification) return null;
  if (
    typeof item.arguments !== "object" ||
    item.arguments === null ||
    Array.isArray(item.arguments)
  ) {
    return null;
  }
  const args = item.arguments as Record<string, unknown>;
  const actual = Object.keys(args).sort();
  const expected = [...specification].sort();
  if (actual.join("|") !== expected.join("|") || !boundedJson(args)) return null;
  if (
    ("signal_id" in args &&
      (typeof args.signal_id !== "string" || !/^BTC-\d{8}-\d{3,}$/.test(args.signal_id))) ||
    ("dedupe_key" in args &&
      (typeof args.dedupe_key !== "string" || !ID_PATTERN.test(args.dedupe_key))) ||
    ("checkpoint_key" in args &&
      (typeof args.checkpoint_key !== "string" || !ID_PATTERN.test(args.checkpoint_key))) ||
    ("business_date" in args &&
      (typeof args.business_date !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(args.business_date)))
  ) {
    return null;
  }
  return { operation: item.operation, arguments: args };
}

export async function handleStateApi(
  request: Request,
  dependencies: AppDependencies,
): Promise<Response | null> {
  const url = new URL(request.url);
  if (!url.pathname.startsWith("/state/v1/")) {
    return null;
  }
  const contentLength = Number.parseInt(request.headers.get("content-length") ?? "0", 10);
  if (Number.isFinite(contentLength) && contentLength > MAX_STATE_BODY_BYTES) {
    return new Response(null, { status: 413 });
  }
  const body = new Uint8Array(await request.arrayBuffer());
  if (body.byteLength > MAX_STATE_BODY_BYTES) {
    return new Response(null, { status: 413 });
  }
  const authorized = await authorizeStateRequest(
    request,
    body,
    dependencies.config.stateApiHmacSecret,
    dependencies.store,
    dependencies.clock,
  );
  if (!authorized) {
    return new Response(null, { status: 401 });
  }

  if (request.method === "GET" && url.pathname === "/state/v1/bootstrap" && body.length === 0) {
    const [paused, summary, runtime] = await Promise.all([
      dependencies.store.getSignalGenerationPaused(),
      dependencies.store.getStatusSummary(),
      dependencies.store.getRuntimeBootstrapState(),
    ]);
    return json({
      schema_version: 1,
      symbol: "BTCUSDT",
      signal_generation_paused: paused,
      latest_health_status: summary.latestHealthStatus,
      latest_health_at: summary.latestHealthAt,
      monitored_signal_ids: runtime.monitoredSignalIds,
      last_signal_at: runtime.lastSignalAt,
      active_managed_signal: runtime.activeManagedSignal,
    });
  }

  if (request.method === "POST" && url.pathname === "/state/v1/notifications") {
    let decoded: unknown;
    try {
      decoded = JSON.parse(new TextDecoder().decode(body));
    } catch {
      return new Response(null, { status: 400 });
    }
    const message = systemNotification(decoded, dependencies.clock.now());
    if (!message) {
      return new Response(null, { status: 400 });
    }
    const inserted = await dependencies.store.enqueueSystemNotification(
      message,
      dependencies.config.telegramAdminUserId,
    );
    // Re-enter delivery even for an existing row: PENDING proves no external
    // call began, while SENT/FAILED/UNKNOWN are terminal and never resent.
    const delivery = await deliverOnce(
      dependencies.store,
      dependencies.sender,
      {
        outboxId: `runtime-${message.dedupeKey}`,
        dedupeKey: message.dedupeKey,
        messageType: message.messageType,
        chatId: dependencies.config.telegramAdminUserId,
        text: message.text,
        createdAt: message.createdAt,
      },
      message.createdAt,
    );
    return json(
      { accepted: true, duplicate: !inserted, delivery_status: delivery.status },
      inserted ? 201 : 200,
    );
  }

  if (request.method === "POST" && url.pathname === "/state/v1/outbox/drain" && body.length === 0) {
    const now = dependencies.clock.now().toISOString();
    const messages = await dependencies.store.listPendingOutbox(25, now);
    const results = [];
    for (const message of messages) {
      const delivery = await deliverOnce(dependencies.store, dependencies.sender, message, now);
      results.push({ outbox_id: message.outboxId, status: delivery.status });
    }
    return json({ drained: results.length, results });
  }

  if (request.method === "POST" && url.pathname === "/state/v1/repository") {
    let decoded: unknown;
    try {
      decoded = JSON.parse(new TextDecoder().decode(body));
    } catch {
      return new Response(null, { status: 400 });
    }
    const command = repositoryCommand(decoded);
    if (!command) return new Response(null, { status: 400 });
    try {
      return json({
        result: await dependencies.store.executeRepositoryCommand(
          command,
          dependencies.config.telegramAdminUserId,
        ),
      });
    } catch {
      return json({ error: "REPOSITORY_COMMAND_REJECTED" }, 409);
    }
  }

  if (request.method === "POST" && url.pathname === "/state/v1/health") {
    let decoded: unknown;
    try {
      decoded = JSON.parse(new TextDecoder().decode(body));
    } catch {
      return new Response(null, { status: 400 });
    }
    const run = healthRun(decoded, dependencies.clock.now());
    if (!run) {
      return new Response(null, { status: 400 });
    }
    const inserted = await dependencies.store.recordHealthRun(run);
    return json({ accepted: true, duplicate: !inserted }, inserted ? 201 : 200);
  }

  return new Response(null, { status: 404 });
}
