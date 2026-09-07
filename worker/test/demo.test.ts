import { describe, expect, it } from "vitest";
import { configurationFailure, handleDemo } from "../src/demo";
import { ConfigurationError } from "../src/config";
import {
  FakeTelegramSender,
  fixedClock,
  MemoryBotStore,
  telegramRequest,
  testConfig,
} from "./helpers";

describe("private scripted demo", () => {
  it("labels fixture results and persists pause across requests", async () => {
    const sender = new FakeTelegramSender();
    const deps = { config: testConfig, store: new MemoryBotStore(), sender, clock: fixedClock };
    await handleDemo(telegramRequest(1, "/pause"), deps);
    await handleDemo(telegramRequest(2, "/status"), deps);
    const text = JSON.stringify(sender.messages);
    expect(text).toContain("SCRIPTED DEMO");
    expect(text).toContain("24");
    expect(text).toContain("NOT strategy evidence");
    expect(text).toContain("PAUSED");
    await handleDemo(telegramRequest(3, "/resume"), deps);
    expect(await deps.store.getSignalGenerationPaused()).toBe(false);
  });

  it("ignores outsiders, groups and rejects unauthenticated requests", async () => {
    const sender = new FakeTelegramSender();
    const deps = { config: testConfig, store: new MemoryBotStore(), sender, clock: fixedClock };
    await handleDemo(telegramRequest(1, "/status", { userId: 111111 }), deps);
    await handleDemo(
      telegramRequest(2, "/status", { chatId: -100123, chatType: "supergroup" }),
      deps,
    );
    expect((await handleDemo(telegramRequest(3, "/status", { secret: null }), deps)).status).toBe(
      401,
    );
    expect(sender.messages).toHaveLength(0);
  });

  it("does not expose the state API and rejects enabled dispatch", async () => {
    const deps = {
      config: testConfig,
      store: new MemoryBotStore(),
      sender: new FakeTelegramSender(),
      clock: fixedClock,
    };
    expect((await handleDemo(new Request("https://demo.example/state/health"), deps)).status).toBe(
      404,
    );
    expect(
      (
        await handleDemo(telegramRequest(1, "/status"), {
          ...deps,
          config: { ...testConfig, productionDispatchEnabled: true },
        })
      ).status,
    ).toBe(503);
  });

  it("reports only safe configuration diagnostics on the health route", async () => {
    const response = configurationFailure(
      new Request("https://demo.example/health"),
      new ConfigurationError("TELEGRAM_BOT_TOKEN is invalid"),
    );
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({
      status: "configuration_error",
      detail: "TELEGRAM_BOT_TOKEN is invalid",
    });

    const hidden = configurationFailure(
      new Request("https://demo.example/telegram/webhook"),
      new Error("sensitive runtime detail"),
    );
    expect(hidden.status).toBe(503);
    expect(await hidden.text()).toBe("");
  });
});
