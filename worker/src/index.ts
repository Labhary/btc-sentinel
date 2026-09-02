import { handleRequest } from "./app";
import { loadConfig, type WorkerEnv } from "./config";
import { GitHubWorkflowDispatcher } from "./dispatch/github";
import { dispatchScheduledWorkflow } from "./dispatch/scheduled";
import { D1BotStore } from "./persistence/d1-store";
import { TelegramApiSender } from "./telegram/sender";
import { systemClock } from "./time";

export default {
  async fetch(request: Request, env: WorkerEnv): Promise<Response> {
    try {
      const config = loadConfig(env);
      return await handleRequest(request, {
        config,
        store: new D1BotStore(env.DB),
        sender: new TelegramApiSender(config.telegramBotToken),
        clock: systemClock,
      });
    } catch (error) {
      console.error(
        JSON.stringify({
          event: "worker_request_failed",
          error_name: error instanceof Error ? error.name : "UnknownError",
        }),
      );
      return Response.json(
        { status: "unavailable" },
        { status: 503, headers: { "cache-control": "no-store" } },
      );
    }
  },
  async scheduled(controller: ScheduledController, env: WorkerEnv, context: ExecutionContext) {
    const config = loadConfig(env);
    const store = new D1BotStore(env.DB);
    const dispatcher = config.githubActionsToken
      ? new GitHubWorkflowDispatcher(config.githubActionsToken)
      : null;
    context.waitUntil(
      dispatchScheduledWorkflow(controller.scheduledTime, config, store, dispatcher, systemClock),
    );
  },
};
