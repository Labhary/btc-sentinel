import type { AppDependencies } from "../app";
import type { HealthRunInput, SystemNotificationInput } from "../contracts";
import { deliverOnce } from "../outbox/delivery";
import { authorizeStateRequest } from "./auth";

const MAX_STATE_BODY_BYTES = 32 * 1024;
const ID_PATTERN = /^[A-Za-z0-9_.:-]{1,128}$/;
const ISO_UTC_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/;

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
