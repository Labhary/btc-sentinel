import { handleRequest, type AppDependencies } from "./app";
import { loadConfig, type WorkerEnv } from "./config";
import { D1BotStore } from "./persistence/d1-store";
import { TelegramApiSender } from "./telegram/sender";
import { systemClock } from "./time";
import report from "./demo-report.json";

export async function handleDemo(request: Request, deps: AppDependencies): Promise<Response> {
  const path = new URL(request.url).pathname;
  if (path !== "/telegram/webhook" && path !== "/health") {
    return new Response(null, { status: 404 });
  }
  if (deps.config.productionDispatchEnabled) {
    return new Response(null, { status: 503 });
  }
  return handleRequest(request, {
    ...deps,
    commandHandler: async (command, store, now) => {
      const label = "PRIVATE SCRIPTED DEMO — artificial prices; no market signals.";
      if (command === "PAUSE" || command === "RESUME") {
        await store.setSignalGenerationPaused(command === "PAUSE", now.toISOString());
        return `${label}\nDemo preference saved. No scheduled engine is running.`;
      }
      if (command === "STATUS") {
        const paused = await store.getSignalGenerationPaused();
        const wins = report.records.filter((row) => row.fixed === "WIN").length;
        return [
          label,
          `Closed scripted trades: ${report.closed_trades}`,
          `Fixture wins: ${wins}; losses: ${report.closed_trades - wins}`,
          "Fixture win rate: 50% (designed test data, NOT strategy evidence)",
          "Targets exceed 2R after modeled costs.",
          `Demo preference: ${paused ? "PAUSED" : "RESUMED"}`,
          "Research win rate: UNPROVEN. Report only after 20+ closed observation trades.",
          "Use /help for the test checklist.",
        ].join("\n");
      }
      return [
        label,
        "Use /status to see the 24 scripted lifecycle results.",
        "Use /pause, then /status; /resume, then /status to test saved preferences.",
        "Restart/redeploy the demo and check that the preference persists.",
        "Messages from other users and groups are ignored.",
        "This demo checks messaging, storage, and scripted lifecycle results.",
        "It does not test live strategy performance or live market collection.",
      ].join("\n");
    },
  });
}

export default {
  async fetch(request: Request, env: WorkerEnv): Promise<Response> {
    try {
      const config = loadConfig(env);
      return await handleDemo(request, {
        config,
        store: new D1BotStore(env.DB),
        sender: new TelegramApiSender(config.telegramBotToken),
        clock: systemClock,
      });
    } catch {
      return new Response(null, { status: 503 });
    }
  },
};
