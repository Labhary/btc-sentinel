import type { WorkerConfig } from "../config";
import type { BotStore, Clock } from "../contracts";
import { isoUtc } from "../time";
import type { WorkflowDispatcher } from "./github";

export type ScheduledResult = "DISABLED" | "DUPLICATE" | "SENT";

function errorCode(error: unknown): string {
  if (error instanceof Error && /^GITHUB_DISPATCH_\d{3}$/.test(error.message)) {
    return error.message;
  }
  return "GITHUB_DISPATCH_UNKNOWN";
}

export async function dispatchScheduledWorkflow(
  scheduledTime: number,
  config: WorkerConfig,
  store: BotStore,
  dispatcher: WorkflowDispatcher | null,
  clock: Clock,
): Promise<ScheduledResult> {
  if (!config.productionDispatchEnabled) {
    return "DISABLED";
  }
  if (!dispatcher) {
    throw new Error("Enabled workflow dispatch has no dispatcher");
  }
  const scheduledAt = isoUtc(new Date(scheduledTime));
  const dispatchKey = `paper-engine:${scheduledAt}`;
  const claimedAt = isoUtc(clock.now());
  const claimed = await store.claimWorkflowDispatch(dispatchKey, scheduledAt, claimedAt);
  if (!claimed) {
    return "DUPLICATE";
  }
  try {
    await dispatcher.dispatch(dispatchKey);
    await store.finishWorkflowDispatch(dispatchKey, "SENT", isoUtc(clock.now()), null);
    return "SENT";
  } catch (error) {
    await store.finishWorkflowDispatch(
      dispatchKey,
      "FAILED",
      isoUtc(clock.now()),
      errorCode(error),
    );
    throw error;
  }
}
