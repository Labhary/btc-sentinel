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

export interface BotStore {
  beginUpdate(updateId: number, receivedAt: string, leaseUntil: string): Promise<boolean>;
  finishUpdate(updateId: number, status: "COMPLETED" | "IGNORED", at: string): Promise<void>;
  getSignalGenerationPaused(): Promise<boolean>;
  setSignalGenerationPaused(paused: boolean, at: string): Promise<void>;
  getStatusSummary(): Promise<StatusSummary>;
  prepareOutbox(message: OutboxMessage): Promise<DeliveryStatus>;
  markOutboxUnknown(outboxId: string, at: string): Promise<void>;
  markOutboxSent(outboxId: string, telegramMessageId: string, at: string): Promise<void>;
  markOutboxFailed(outboxId: string, errorCode: string, at: string): Promise<void>;
  recordCommand(
    updateId: number,
    command: CommandName,
    occurredAt: string,
    result: CommandAuditResult,
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
