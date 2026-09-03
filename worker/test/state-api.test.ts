import { describe, expect, it } from "vitest";

import { handleRequest } from "../src/app";
import { signStateRequestForTest } from "../src/state-api/auth";
import { FakeTelegramSender, fixedClock, MemoryBotStore, testConfig } from "./helpers";

const encoder = new TextEncoder();

function dependencies(store: MemoryBotStore) {
  return { config: testConfig, store, sender: new FakeTelegramSender(), clock: fixedClock };
}

async function signedRequest(
  path: string,
  method: "GET" | "POST",
  value: unknown,
  nonce: string,
  timestamp = Math.floor(fixedClock.now().getTime() / 1000).toString(),
): Promise<Request> {
  const text = value === null ? "" : JSON.stringify(value);
  const body = encoder.encode(text);
  const signature = await signStateRequestForTest(
    method,
    path,
    timestamp,
    nonce,
    body,
    testConfig.stateApiHmacSecret,
  );
  return new Request(`https://worker.example${path}`, {
    method,
    headers: {
      "content-type": "application/json",
      "x-btc-timestamp": timestamp,
      "x-btc-nonce": nonce,
      "x-btc-signature": signature,
    },
    ...(text === "" ? {} : { body: text }),
  });
}

describe("signed state API", () => {
  it("returns only bounded bootstrap state after authentication", async () => {
    const store = new MemoryBotStore();
    store.paused = true;
    const response = await handleRequest(
      await signedRequest("/state/v1/bootstrap", "GET", null, "nonce-bootstrap-0001"),
      dependencies(store),
    );

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      schema_version: 1,
      symbol: "BTCUSDT",
      signal_generation_paused: true,
      latest_health_status: null,
      latest_health_at: null,
      monitored_signal_ids: [],
      last_signal_at: null,
      active_managed_signal: false,
    });
  });

  it("rejects altered, stale, replayed, and query-bearing requests", async () => {
    const store = new MemoryBotStore();
    const valid = await signedRequest("/state/v1/bootstrap", "GET", null, "nonce-replay-safe-01");
    expect((await handleRequest(valid, dependencies(store))).status).toBe(200);
    const replay = await signedRequest("/state/v1/bootstrap", "GET", null, "nonce-replay-safe-01");
    expect((await handleRequest(replay, dependencies(store))).status).toBe(401);

    const staleTimestamp = Math.floor(fixedClock.now().getTime() / 1000 - 301).toString();
    const stale = await signedRequest(
      "/state/v1/bootstrap",
      "GET",
      null,
      "nonce-stale-safe-001",
      staleTimestamp,
    );
    expect((await handleRequest(stale, dependencies(store))).status).toBe(401);

    const querySignedForPath = await signedRequest(
      "/state/v1/bootstrap?leak=true",
      "GET",
      null,
      "nonce-query-safe-001",
    );
    expect((await handleRequest(querySignedForPath, dependencies(store))).status).toBe(401);

    const altered = await signedRequest(
      "/state/v1/health",
      "POST",
      { value: 1 },
      "nonce-altered-00001",
    );
    altered.headers.set("x-btc-signature", "0".repeat(64));
    expect((await handleRequest(altered, dependencies(store))).status).toBe(401);
  });

  it("records a strict completed health payload idempotently", async () => {
    const store = new MemoryBotStore();
    const payload = {
      run_id: "run-20260802-001",
      job_name: "paper-engine",
      started_at: "2026-08-02T12:30:00.000Z",
      finished_at: "2026-08-02T12:34:00.000Z",
      status: "DEGRADED",
      data_fresh: false,
      summary: { reason: "runtime_not_activated" },
      dedupe_key: "health:run-20260802-001",
    };
    const first = await handleRequest(
      await signedRequest("/state/v1/health", "POST", payload, "nonce-health-safe-001"),
      dependencies(store),
    );
    const duplicate = await handleRequest(
      await signedRequest("/state/v1/health", "POST", payload, "nonce-health-safe-002"),
      dependencies(store),
    );

    expect(first.status).toBe(201);
    expect(await first.json()).toEqual({ accepted: true, duplicate: false });
    expect(duplicate.status).toBe(200);
    expect(await duplicate.json()).toEqual({ accepted: true, duplicate: true });
    expect(store.healthRuns).toHaveLength(1);
  });

  it("queues a typed owner notification exactly once", async () => {
    const store = new MemoryBotStore();
    const sender = new FakeTelegramSender();
    const deps = { ...dependencies(store), sender };
    const payload = {
      message_type: "REPORT",
      text: "Daily paper report",
      dedupe_key: "report:daily:2026-08-02:v0.10.0",
      signal_id: null,
      created_at: "2026-08-02T12:34:00.000Z",
    };
    const first = await handleRequest(
      await signedRequest("/state/v1/notifications", "POST", payload, "nonce-notification-001"),
      deps,
    );
    const duplicate = await handleRequest(
      await signedRequest("/state/v1/notifications", "POST", payload, "nonce-notification-002"),
      deps,
    );
    expect(first.status).toBe(201);
    expect(duplicate.status).toBe(200);
    expect(store.systemNotifications.size).toBe(1);
    expect(store.systemNotifications.values().next().value?.chatId).toBe("424242");
    expect(sender.messages).toEqual([{ chatId: "424242", text: "Daily paper report" }]);
  });

  it("drains bounded pending outbox rows through the authenticated owner sender", async () => {
    const store = new MemoryBotStore();
    const sender = new FakeTelegramSender();
    await store.prepareOutbox({
      outboxId: "runtime-notify:signal:BTC-20260802-001:created",
      dedupeKey: "notify:signal:BTC-20260802-001:created",
      messageType: "SIGNAL",
      chatId: "424242",
      text: "paper signal",
      createdAt: "2026-08-02T12:34:00.000Z",
    });
    const response = await handleRequest(
      await signedRequest("/state/v1/outbox/drain", "POST", null, "nonce-outbox-drain-01"),
      { ...dependencies(store), sender },
    );
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      drained: 1,
      results: [
        {
          outbox_id: "runtime-notify:signal:BTC-20260802-001:created",
          status: "SENT",
        },
      ],
    });
    expect(sender.messages).toEqual([{ chatId: "424242", text: "paper signal" }]);
  });

  it("rejects unknown notification types and future notification times", async () => {
    const store = new MemoryBotStore();
    for (const [index, overrides] of [
      { message_type: "ORDER" },
      { created_at: "2026-08-02T12:40:00.000Z" },
      { text: "x".repeat(4097) },
    ].entries()) {
      const response = await handleRequest(
        await signedRequest(
          "/state/v1/notifications",
          "POST",
          {
            message_type: "SIGNAL",
            text: "paper signal",
            dedupe_key: `notify:test:${index}`,
            signal_id: "BTC-20260802-001",
            created_at: "2026-08-02T12:34:00.000Z",
            ...overrides,
          },
          `nonce-notification-bad-${index}`,
        ),
        dependencies(store),
      );
      expect(response.status).toBe(400);
    }
  });

  it("accepts only exact allowlisted repository command shapes", async () => {
    const store = new MemoryBotStore();
    const accepted = await handleRequest(
      await signedRequest(
        "/state/v1/repository",
        "POST",
        { operation: "get_signal_status", arguments: { signal_id: "BTC-20260802-001" } },
        "nonce-repository-good-01",
      ),
      dependencies(store),
    );
    expect(accepted.status).toBe(200);
    expect(store.repositoryCommands).toEqual([
      { operation: "get_signal_status", arguments: { signal_id: "BTC-20260802-001" } },
    ]);

    for (const [index, payload] of [
      { operation: "execute_sql", arguments: {} },
      {
        operation: "get_signal_status",
        arguments: { signal_id: "BTC-20260802-001", table: "signals" },
      },
      { operation: "get_signal_status", arguments: {} },
      { operation: "get_signal_status", arguments: { signal_id: { nested: { too: [] } } } },
    ].entries()) {
      const response = await handleRequest(
        await signedRequest(
          "/state/v1/repository",
          "POST",
          payload,
          `nonce-repository-bad-${index}`,
        ),
        dependencies(store),
      );
      expect(response.status).toBe(400);
    }
    expect(store.repositoryCommands).toHaveLength(1);
  });

  it("rejects unknown fields and oversized bodies", async () => {
    const store = new MemoryBotStore();
    const invalid = await handleRequest(
      await signedRequest(
        "/state/v1/health",
        "POST",
        { unexpected: true },
        "nonce-invalid-safe-01",
      ),
      dependencies(store),
    );
    expect(invalid.status).toBe(400);

    const oversized = await signedRequest(
      "/state/v1/health",
      "POST",
      { padding: "x".repeat(33 * 1024) },
      "nonce-oversize-safe1",
    );
    expect((await handleRequest(oversized, dependencies(store))).status).toBe(413);
  });

  it("rejects impossible and future health timestamps", async () => {
    const store = new MemoryBotStore();
    const base = {
      run_id: "run-time-invalid",
      job_name: "paper-engine",
      started_at: "2026-08-02T12:30:00.000Z",
      finished_at: "2026-08-02T12:34:00.000Z",
      status: "FAILED",
      data_fresh: false,
      summary: {},
      dedupe_key: "health:run-time-invalid",
    };
    for (const [index, times] of [
      { started_at: "2026-99-02T12:30:00.000Z" },
      { finished_at: "2026-08-02T12:40:00.000Z" },
      {
        started_at: "2026-08-02T12:35:00.000Z",
        finished_at: "2026-08-02T12:34:00.000Z",
      },
    ].entries()) {
      const response = await handleRequest(
        await signedRequest(
          "/state/v1/health",
          "POST",
          { ...base, ...times },
          `nonce-time-invalid-${index}`,
        ),
        dependencies(store),
      );
      expect(response.status).toBe(400);
    }
  });
});
