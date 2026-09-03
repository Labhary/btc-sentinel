import type { BotStore, CommandName } from "../contracts";
import { formatCasablanca } from "../time";

const DISCLAIMER = "Paper analysis only — never a guarantee or automatic trade.";

function helpMessage(): string {
  return [
    "BTC Sentinel commands",
    "",
    "/status — bot and stored-signal status",
    "/pause — pause new signals and routine reports",
    "/resume — resume new signals and routine reports",
    "/help — show this list",
    "",
    "Active paper-trade monitoring will continue while new signals are paused.",
    DISCLAIMER,
  ].join("\n");
}

export async function executeCommand(
  command: CommandName,
  store: BotStore,
  now: Date,
): Promise<string> {
  switch (command) {
    case "START":
      return [
        "BTC Sentinel is connected.",
        "",
        "Market: BTC/USDT only",
        "Mode: paper analysis — no Binance order permissions",
        "Phase: production runtime integration; activation remains disabled",
        "",
        "Use /help to see the available commands.",
        DISCLAIMER,
      ].join("\n");
    case "HELP":
      return helpMessage();
    case "PAUSE":
      await store.setSignalGenerationPaused(true, now.toISOString());
      return [
        "New signals and routine reports are PAUSED.",
        "Active paper-trade tracking will continue for safety.",
      ].join("\n");
    case "RESUME":
      await store.setSignalGenerationPaused(false, now.toISOString());
      return "New signals and routine reports are RESUMED.";
    case "STATUS": {
      const [paused, summary] = await Promise.all([
        store.getSignalGenerationPaused(),
        store.getStatusSummary(),
      ]);
      const health = summary.latestHealthStatus
        ? `${summary.latestHealthStatus} at ${summary.latestHealthAt ?? "unknown time"}`
        : "No engine run yet";
      return [
        "BTC Sentinel status",
        "",
        "Market: BTC/USDT",
        "Mode: PAPER ONLY",
        `New signals: ${paused ? "PAUSED" : "ENABLED"}`,
        "Market engine: NOT ACTIVATED (Phase 12 safety gate)",
        `Pending: ${summary.pendingSignals}`,
        `Active: ${summary.activeSignals}`,
        `Closed: ${summary.closedSignals}`,
        `Expired: ${summary.expiredSignals}`,
        `Cancelled: ${summary.cancelledSignals}`,
        `Latest health: ${health}`,
        `Casablanca time: ${formatCasablanca(now)}`,
      ].join("\n");
    }
    case "UNKNOWN":
      return "Unknown command. Use /help.";
  }
}
