import type { BotStore, Clock, OutboxMessage, TelegramSender } from "./contracts";
import type { WorkerConfig } from "./config";
import { deliverOnce } from "./outbox/delivery";
import { secureEquals } from "./security";
import { executeCommand } from "./telegram/commands";
import { isAuthorizedPrivateOwner, parseCommand, parseTelegramUpdate } from "./telegram/update";
import { addMinutes, isoUtc } from "./time";

const WEBHOOK_SECRET_HEADER = "x-telegram-bot-api-secret-token";
const MAX_UPDATE_BYTES = 128 * 1024;

export interface AppDependencies {
  config: WorkerConfig;
  store: BotStore;
  sender: TelegramSender;
  clock: Clock;
}

function noContent(): Response {
  return new Response(null, { status: 204 });
}

function jsonResponse(body: unknown, status = 200): Response {
  return Response.json(body, {
    status,
    headers: { "cache-control": "no-store" },
  });
}

async function telegramWebhook(request: Request, dependencies: AppDependencies): Promise<Response> {
  const secretValid = await secureEquals(
    request.headers.get(WEBHOOK_SECRET_HEADER),
    dependencies.config.telegramWebhookSecret,
  );
  if (!secretValid) {
    return new Response(null, { status: 401 });
  }

  const contentLength = Number.parseInt(request.headers.get("content-length") ?? "0", 10);
  if (Number.isFinite(contentLength) && contentLength > MAX_UPDATE_BYTES) {
    return new Response(null, { status: 413 });
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return new Response(null, { status: 400 });
  }
  const update = parseTelegramUpdate(body);
  if (!update) {
    return new Response(null, { status: 400 });
  }

  const now = dependencies.clock.now();
  const nowIso = isoUtc(now);
  const claimed = await dependencies.store.beginUpdate(
    update.updateId,
    nowIso,
    isoUtc(addMinutes(now, 2)),
  );
  if (!claimed) {
    return noContent();
  }

  if (
    !update.message ||
    !isAuthorizedPrivateOwner(update.message, dependencies.config.telegramAdminUserId)
  ) {
    await dependencies.store.finishUpdate(update.updateId, "IGNORED", nowIso);
    return noContent();
  }
  const command = parseCommand(update.message.text);
  if (!command) {
    await dependencies.store.finishUpdate(update.updateId, "IGNORED", nowIso);
    return noContent();
  }

  const text = await executeCommand(command.name, dependencies.store, now);
  const message: OutboxMessage = {
    outboxId: `telegram-response-${update.updateId}`,
    dedupeKey: `telegram:update:${update.updateId}:response`,
    messageType: "COMMAND_RESPONSE",
    chatId: update.message.chatId,
    text,
    createdAt: nowIso,
  };
  const delivery = await deliverOnce(dependencies.store, dependencies.sender, message, nowIso);
  await dependencies.store.recordCommand(
    update.updateId,
    command.name,
    nowIso,
    delivery.auditResult,
  );
  await dependencies.store.finishUpdate(update.updateId, "COMPLETED", nowIso);
  return noContent();
}

export async function handleRequest(
  request: Request,
  dependencies: AppDependencies,
): Promise<Response> {
  const url = new URL(request.url);
  if (request.method === "GET" && url.pathname === "/health") {
    return jsonResponse({ status: "ok", phase: 2, symbol: "BTCUSDT" });
  }
  if (request.method === "POST" && url.pathname === "/telegram/webhook") {
    return telegramWebhook(request, dependencies);
  }
  return new Response(null, { status: 404 });
}
