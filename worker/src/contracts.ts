export type CommandName = "START" | "HELP" | "STATUS" | "PAUSE" | "RESUME" | "UNKNOWN";

export type CommandAuditResult = "SENT" | "DELIVERY_FAILED" | "DELIVERY_UNKNOWN" | "NO_RESPONSE";

export type DeliveryStatus = "PENDING" | "SENDING" | "SENT" | "FAILED" | "UNKNOWN";

export interface StatusSummary {
  pendingSignals: number;
  activeSignals: number;
  closedSignals: number;
  expiredSignals: number;
  cancelledSignals: number;
  latestHealthStatus: string | null;
  latestHealthAt: string | null;
}

export interface OutboxMessage {
  outboxId: string;
  dedupeKey: string;
  messageType: string;
  chatId: string;
  text: string;
  createdAt: string;
}

export interface HealthRunInput {
  runId: string;
  jobName: string;
  startedAt: string;
  finishedAt: string;
  status: "OK" | "DEGRADED" | "FAILED";
  dataFresh: boolean;
  summary: Record<string, unknown>;
  dedupeKey: string;
}

export interface RuntimeBootstrapState {
  monitoredSignalIds: string[];
  lastSignalAt: string | null;
  activeManagedSignal: boolean;
}

export interface SystemNotificationInput {
  messageType: "SIGNAL" | "LIFECYCLE" | "MANAGEMENT" | "REPORT";
  text: string;
  dedupeKey: string;
  signalId: string | null;
  createdAt: string;
}

export interface RepositoryCommand {
  operation: string;
  arguments: Record<string, unknown>;
}

export interface BotStore {
  beginUpdate(updateId: number, receivedAt: string, leaseUntil: string): Promise<boolean>;
  finishUpdate(updateId: number, status: "COMPLETED" | "IGNORED", at: string): Promise<void>;
  getSignalGenerationPaused(): Promise<boolean>;
  setSignalGenerationPaused(paused: boolean, at: string): Promise<void>;
  getStatusSummary(): Promise<StatusSummary>;
  getRuntimeBootstrapState(): Promise<RuntimeBootstrapState>;
  prepareOutbox(message: OutboxMessage): Promise<DeliveryStatus>;
  listPendingOutbox(limit: number, availableAt: string): Promise<OutboxMessage[]>;
  markOutboxUnknown(outboxId: string, at: string): Promise<void>;
  markOutboxSent(outboxId: string, telegramMessageId: string, at: string): Promise<void>;
  markOutboxFailed(outboxId: string, errorCode: string, at: string): Promise<void>;
  recordCommand(
    updateId: number,
    command: CommandName,
    occurredAt: string,
    result: CommandAuditResult,
  ): Promise<void>;
  claimStateNonce(nonce: string, expiresAt: string, createdAt: string): Promise<boolean>;
  recordHealthRun(run: HealthRunInput): Promise<boolean>;
  enqueueSystemNotification(message: SystemNotificationInput, chatId: string): Promise<boolean>;
  executeRepositoryCommand(command: RepositoryCommand, ownerChatId: string): Promise<unknown>;
  claimWorkflowDispatch(
    dispatchKey: string,
    scheduledAt: string,
    claimedAt: string,
  ): Promise<boolean>;
  finishWorkflowDispatch(
    dispatchKey: string,
    status: "SENT" | "FAILED",
    finishedAt: string,
    errorCode: string | null,
  ): Promise<void>;
}

export interface SentTelegramMessage {
  messageId: string;
}

export interface TelegramSender {
  sendMessage(chatId: string, text: string): Promise<SentTelegramMessage>;
}

export interface Clock {
  now(): Date;
}
