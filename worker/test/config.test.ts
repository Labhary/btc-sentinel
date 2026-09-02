import { describe, expect, it } from "vitest";

import { ConfigurationError, loadConfig, type WorkerEnv } from "../src/config";

function validEnvironment(): WorkerEnv {
  return {
    DB: {} as D1Database,
    TELEGRAM_BOT_TOKEN: "123456789" + ":" + "A".repeat(32),
    TELEGRAM_ADMIN_USER_ID: "424242",
    TELEGRAM_WEBHOOK_SECRET: "W".repeat(32),
    STATE_API_HMAC_SECRET: "H".repeat(32),
  };
}

describe("Worker configuration", () => {
  it("loads valid values without rewriting secrets", () => {
    const environment = validEnvironment();
    const config = loadConfig(environment);

    expect(config.telegramBotToken).toBe(environment.TELEGRAM_BOT_TOKEN);
    expect(config.telegramAdminUserId).toBe("424242");
  });

  it.each([
    ["missing token", { TELEGRAM_BOT_TOKEN: "" }],
    ["invalid token", { TELEGRAM_BOT_TOKEN: "not-a-token" }],
    ["zero admin", { TELEGRAM_ADMIN_USER_ID: "0" }],
    ["unsafe admin", { TELEGRAM_ADMIN_USER_ID: "9999999999999999" }],
    ["short webhook secret", { TELEGRAM_WEBHOOK_SECRET: "short" }],
    ["invalid webhook alphabet", { TELEGRAM_WEBHOOK_SECRET: "!".repeat(32) }],
    ["short state secret", { STATE_API_HMAC_SECRET: "short" }],
  ])("rejects %s", (_name, overrides) => {
    expect(() => loadConfig({ ...validEnvironment(), ...overrides })).toThrow(ConfigurationError);
  });

  it("requires independent webhook and state API secrets", () => {
    const environment = validEnvironment();
    environment.STATE_API_HMAC_SECRET = environment.TELEGRAM_WEBHOOK_SECRET;

    expect(() => loadConfig(environment)).toThrow("must differ");
  });

  it("keeps production dispatch disabled by default", () => {
    const config = loadConfig(validEnvironment());
    expect(config.productionDispatchEnabled).toBe(false);
    expect(config.githubActionsToken).toBeNull();
  });

  it("requires a token only when production dispatch is explicitly enabled", () => {
    expect(() =>
      loadConfig({ ...validEnvironment(), PRODUCTION_DISPATCH_ENABLED: "true" }),
    ).toThrow(/GITHUB_ACTIONS_TOKEN/);
    const config = loadConfig({
      ...validEnvironment(),
      PRODUCTION_DISPATCH_ENABLED: "true",
      GITHUB_ACTIONS_TOKEN: "github-actions-token-value",
    });
    expect(config.productionDispatchEnabled).toBe(true);
  });

  it("rejects ambiguous dispatch flags", () => {
    expect(() => loadConfig({ ...validEnvironment(), PRODUCTION_DISPATCH_ENABLED: "yes" })).toThrow(
      /true or false/,
    );
  });
});
