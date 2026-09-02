import { describe, expect, it } from "vitest";

import type { WorkflowDispatcher } from "../src/dispatch/github";
import { dispatchScheduledWorkflow } from "../src/dispatch/scheduled";
import { fixedClock, MemoryBotStore, testConfig } from "./helpers";

class FakeDispatcher implements WorkflowDispatcher {
  readonly keys: string[] = [];

  constructor(private readonly failure: Error | null = null) {}

  async dispatch(dispatchKey: string): Promise<void> {
    this.keys.push(dispatchKey);
    if (this.failure) {
      throw this.failure;
    }
  }
}

describe("scheduled workflow dispatch", () => {
  const enabled = {
    ...testConfig,
    productionDispatchEnabled: true,
    githubActionsToken: "not-exposed-to-dispatch-service",
  };

  it("does nothing while the explicit production gate is disabled", async () => {
    const store = new MemoryBotStore();
    const dispatcher = new FakeDispatcher();
    await expect(
      dispatchScheduledWorkflow(fixedClock.now().getTime(), testConfig, store, null, fixedClock),
    ).resolves.toBe("DISABLED");
    expect(dispatcher.keys).toHaveLength(0);
    expect(store.dispatches.size).toBe(0);
  });

  it("dispatches one fixed workflow and deduplicates the same cron instant", async () => {
    const store = new MemoryBotStore();
    const dispatcher = new FakeDispatcher();
    const scheduledTime = fixedClock.now().getTime();
    await expect(
      dispatchScheduledWorkflow(scheduledTime, enabled, store, dispatcher, fixedClock),
    ).resolves.toBe("SENT");
    await expect(
      dispatchScheduledWorkflow(scheduledTime, enabled, store, dispatcher, fixedClock),
    ).resolves.toBe("DUPLICATE");

    expect(dispatcher.keys).toEqual(["paper-engine:2026-08-02T12:34:56.000Z"]);
    expect(store.dispatches.values().next().value).toMatchObject({ status: "SENT" });
  });

  it("records a bounded failure code and does not retry the same dispatch blindly", async () => {
    const store = new MemoryBotStore();
    const dispatcher = new FakeDispatcher(new Error("GITHUB_DISPATCH_403"));
    const scheduledTime = fixedClock.now().getTime();
    await expect(
      dispatchScheduledWorkflow(scheduledTime, enabled, store, dispatcher, fixedClock),
    ).rejects.toThrow("GITHUB_DISPATCH_403");
    await expect(
      dispatchScheduledWorkflow(scheduledTime, enabled, store, dispatcher, fixedClock),
    ).resolves.toBe("DUPLICATE");
    expect(store.dispatches.values().next().value).toMatchObject({
      status: "FAILED",
      errorCode: "GITHUB_DISPATCH_403",
    });
  });
});
