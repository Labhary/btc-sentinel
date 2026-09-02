import { describe, expect, it } from "vitest";

import { handleRequest } from "../src/app";
import {
  FakeTelegramSender,
  fixedClock,
  MemoryBotStore,
  telegramRequest,
  testConfig,
} from "./helpers";

function dependencies(store = new MemoryBotStore(), sender = new FakeTelegramSender()) {
  return { config: testConfig, store, sender, clock: fixedClock };
}

describe("Worker routes", () => {
  it("returns a minimal non-secret health document", async () => {
    const response = await handleRequest(
      new Request("https://worker.example/health"),
      dependencies(),
    );

    const body = await response.text();
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(JSON.parse(body)).toEqual({
      status: "ok",
      phase: 12,
      symbol: "BTCUSDT",
      production_dispatch_enabled: false,
    });
    expect(body).not.toContain(testConfig.telegramWebhookSecret);
  });

  it("rejects a missing or incorrect webhook secret before touching state", async () => {
    for (const secret of [null, "incorrect-secret"]) {
      const store = new MemoryBotStore();
      const sender = new FakeTelegramSender();
      const response = await handleRequest(
        telegramRequest(1, "/start", { secret }),
        dependencies(store, sender),
      );

      expect(response.status).toBe(401);
      expect(store.updates.size).toBe(0);
      expect(sender.messages).toHaveLength(0);
    }
  });

  it("rejects malformed JSON", async () => {
    const request = new Request("https://worker.example/telegram/webhook", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-telegram-bot-api-secret-token": testConfig.telegramWebhookSecret,
      },
      body: "{",
    });

    const response = await handleRequest(request, dependencies());
    expect(response.status).toBe(400);
  });

  it("rejects an oversized update before parsing it", async () => {
    const store = new MemoryBotStore();
    const request = telegramRequest(1, "/start");
    request.headers.set("content-length", String(129 * 1024));

    const response = await handleRequest(request, dependencies(store));

    expect(response.status).toBe(413);
    expect(store.updates.size).toBe(0);
  });

  it("ignores updates from another user and group messages", async () => {
    const store = new MemoryBotStore();
    const sender = new FakeTelegramSender();

    const outsider = await handleRequest(
      telegramRequest(2, "/status", { userId: 111111 }),
      dependencies(store, sender),
    );
    const group = await handleRequest(
      telegramRequest(3, "/status", { chatId: -100123, chatType: "supergroup" }),
      dependencies(store, sender),
    );

    expect(outsider.status).toBe(204);
    expect(group.status).toBe(204);
    expect(store.updates.get(2)?.status).toBe("IGNORED");
    expect(store.updates.get(3)?.status).toBe("IGNORED");
    expect(sender.messages).toHaveLength(0);
  });

  it("sends one start response for duplicate Telegram updates", async () => {
    const store = new MemoryBotStore();
    const sender = new FakeTelegramSender();
    const deps = dependencies(store, sender);

    const first = await handleRequest(telegramRequest(10, "/start"), deps);
    const duplicate = await handleRequest(telegramRequest(10, "/start"), deps);

    expect(first.status).toBe(204);
    expect(duplicate.status).toBe(204);
    expect(sender.messages).toHaveLength(1);
    expect(sender.messages[0]?.text).toContain("BTC Sentinel is connected");
    expect(store.outbox.get("telegram:update:10:response")?.status).toBe("SENT");
    expect(store.updates.get(10)).toMatchObject({ status: "COMPLETED", attempts: 1 });
    expect(store.commands).toEqual([
      expect.objectContaining({ updateId: 10, command: "START", result: "SENT" }),
    ]);
  });

  it("reclaims an update after an abandoned processing lease expires", async () => {
    const store = new MemoryBotStore();
    await store.beginUpdate(11, "2026-08-02T12:00:00.000Z", "2026-08-02T12:01:00.000Z");
    const sender = new FakeTelegramSender();

    await handleRequest(telegramRequest(11, "/start"), dependencies(store, sender));

    expect(sender.messages).toHaveLength(1);
    expect(store.updates.get(11)).toMatchObject({ status: "COMPLETED", attempts: 2 });
  });

  it("pauses only new signals, reports status, and resumes", async () => {
    const store = new MemoryBotStore();
    store.summary = {
      pendingSignals: 1,
      activeSignals: 2,
      closedSignals: 3,
      expiredSignals: 4,
      cancelledSignals: 5,
      latestHealthStatus: "OK",
      latestHealthAt: "2026-08-02T12:30:00.000Z",
    };
    const sender = new FakeTelegramSender();
    const deps = dependencies(store, sender);

    await handleRequest(telegramRequest(20, "/pause"), deps);
    expect(store.paused).toBe(true);
    expect(sender.messages[0]?.text).toContain("Active paper-trade tracking will continue");

    await handleRequest(telegramRequest(21, "/status"), deps);
    expect(sender.messages[1]?.text).toContain("New signals: PAUSED");
    expect(sender.messages[1]?.text).toContain("Active: 2");
    expect(sender.messages[1]?.text).toContain("Casablanca time:");

    await handleRequest(telegramRequest(22, "/resume"), deps);
    expect(store.paused).toBe(false);
    expect(sender.messages[2]?.text).toContain("RESUMED");
  });

  it("does not blindly resend when Telegram delivery is uncertain", async () => {
    const store = new MemoryBotStore();
    const sender = new FakeTelegramSender("uncertain");
    const deps = dependencies(store, sender);

    await handleRequest(telegramRequest(30, "/help"), deps);
    await handleRequest(telegramRequest(30, "/help"), deps);

    expect(sender.messages).toHaveLength(1);
    expect(store.outbox.get("telegram:update:30:response")?.status).toBe("UNKNOWN");
    expect(store.commands[0]?.result).toBe("DELIVERY_UNKNOWN");
    expect(store.updates.get(30)?.status).toBe("COMPLETED");
  });

  it("records a known Telegram rejection as failed", async () => {
    const store = new MemoryBotStore();
    const sender = new FakeTelegramSender("rejected");

    await handleRequest(telegramRequest(31, "/help"), dependencies(store, sender));

    expect(store.outbox.get("telegram:update:31:response")).toMatchObject({
      status: "FAILED",
      errorCode: "TELEGRAM_403",
    });
    expect(store.commands[0]?.result).toBe("DELIVERY_FAILED");
  });

  it("answers unknown slash commands and ignores ordinary text", async () => {
    const store = new MemoryBotStore();
    const sender = new FakeTelegramSender();
    const deps = dependencies(store, sender);

    await handleRequest(telegramRequest(40, "/doesnotexist"), deps);
    await handleRequest(telegramRequest(41, "hello"), deps);

    expect(sender.messages).toHaveLength(1);
    expect(sender.messages[0]?.text).toBe("Unknown command. Use /help.");
    expect(store.commands[0]?.command).toBe("UNKNOWN");
    expect(store.updates.get(41)?.status).toBe("IGNORED");
  });
});
