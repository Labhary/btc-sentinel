import type {
  BotStore,
  Clock,
  CommandAuditResult,
  CommandName,
  DeliveryStatus,
  OutboxMessage,
  SentTelegramMessage,
  StatusSummary,
  TelegramSender,
} from "../src/contracts";
import type { WorkerConfig } from "../src/config";
import { TelegramRejectedError } from "../src/telegram/sender";

interface StoredUpdate {
  status: "PROCESSING" | "COMPLETED" | "IGNORED";
  leaseUntil: string | null;
  attempts: number;
}

interface StoredOutbox {
  message: OutboxMessage;
  status: DeliveryStatus;
  telegramMessageId: string | null;
  errorCode: string | null;
}

export interface RecordedCommand {
  updateId: number;
  command: CommandName;
  occurredAt: string;
  result: CommandAuditResult;
}

export class MemoryBotStore implements BotStore {
  readonly updates = new Map<number, StoredUpdate>();
  readonly outbox = new Map<string, StoredOutbox>();
  readonly commands: RecordedCommand[] = [];
  paused = false;
  summary: StatusSummary = {
    pendingSignals: 0,
    activeSignals: 0,
    closedSignals: 0,
    expiredSignals: 0,
    cancelledSignals: 0,
    latestHealthStatus: null,
    latestHealthAt: null,
  };

  async beginUpdate(updateId: number, receivedAt: string, leaseUntil: string): Promise<boolean> {
    const current = this.updates.get(updateId);
    if (
      current?.status === "COMPLETED" ||
      current?.status === "IGNORED" ||
      (current?.status === "PROCESSING" &&
        current.leaseUntil !== null &&
        current.leaseUntil >= receivedAt)
    ) {
      return false;
    }
    this.updates.set(updateId, {
      status: "PROCESSING",
      leaseUntil,
      attempts: (current?.attempts ?? 0) + 1,
    });
    return true;
  }

  async finishUpdate(
    updateId: number,
    status: "COMPLETED" | "IGNORED",
    _at: string,
  ): Promise<void> {
    const current = this.updates.get(updateId);
    if (current?.status === "PROCESSING") {
      this.updates.set(updateId, { ...current, status, leaseUntil: null });
    }
  }

  async getSignalGenerationPaused(): Promise<boolean> {
    return this.paused;
  }

  async setSignalGenerationPaused(paused: boolean, _at: string): Promise<void> {
    this.paused = paused;
  }

  async getStatusSummary(): Promise<StatusSummary> {
    return this.summary;
  }

  async prepareOutbox(message: OutboxMessage): Promise<DeliveryStatus> {
    const existing = this.outbox.get(message.dedupeKey);
    if (existing) {
      return existing.status;
    }
    this.outbox.set(message.dedupeKey, {
      message,
      status: "PENDING",
      telegramMessageId: null,
      errorCode: null,
    });
    return "PENDING";
  }

  async markOutboxUnknown(outboxId: string, _at: string): Promise<void> {
    const item = this.findOutbox(outboxId);
    if (item.status === "PENDING") {
      item.status = "UNKNOWN";
    }
  }

  async markOutboxSent(outboxId: string, telegramMessageId: string, _at: string): Promise<void> {
    const item = this.findOutbox(outboxId);
    if (item.status === "UNKNOWN") {
      item.status = "SENT";
      item.telegramMessageId = telegramMessageId;
    }
  }

  async markOutboxFailed(outboxId: string, errorCode: string, _at: string): Promise<void> {
    const item = this.findOutbox(outboxId);
    if (item.status === "UNKNOWN") {
      item.status = "FAILED";
      item.errorCode = errorCode;
    }
  }

  async recordCommand(
    updateId: number,
    command: CommandName,
    occurredAt: string,
    result: CommandAuditResult,
  ): Promise<void> {
    if (!this.commands.some((item) => item.updateId === updateId)) {
      this.commands.push({ updateId, command, occurredAt, result });
    }
  }

  private findOutbox(outboxId: string): StoredOutbox {
    const item = [...this.outbox.values()].find(
      (candidate) => candidate.message.outboxId === outboxId,
    );
    if (!item) {
      throw new Error(`Missing outbox item ${outboxId}`);
    }
    return item;
  }
}

export type SenderMode = "success" | "rejected" | "uncertain";

export class FakeTelegramSender implements TelegramSender {
  readonly messages: Array<{ chatId: string; text: string }> = [];

  constructor(readonly mode: SenderMode = "success") {}

  async sendMessage(chatId: string, text: string): Promise<SentTelegramMessage> {
    this.messages.push({ chatId, text });
    if (this.mode === "rejected") {
      throw new TelegramRejectedError("TELEGRAM_403");
    }
    if (this.mode === "uncertain") {
      throw new Error("network state unknown");
    }
    return { messageId: String(1000 + this.messages.length) };
  }
}

export const fixedDate = new Date("2026-08-02T12:34:56.000Z");

export const fixedClock: Clock = {
  now: () => new Date(fixedDate),
};

export const testConfig: WorkerConfig = {
  telegramBotToken: "not-used-by-fake-sender",
  telegramAdminUserId: "424242",
  telegramWebhookSecret: "webhook_secret_abcdefghijklmnopqrstuvwxyz",
  stateApiHmacSecret: "state_api_secret_abcdefghijklmnopqrstuvwxyz",
};

export function telegramRequest(
  updateId: number,
  text: string | null,
  options: {
    userId?: number;
    chatId?: number;
    chatType?: string;
    secret?: string | null;
  } = {},
): Request {
  const userId = options.userId ?? 424242;
  const chatId = options.chatId ?? userId;
  const headers = new Headers({ "content-type": "application/json" });
  const secret = options.secret === undefined ? testConfig.telegramWebhookSecret : options.secret;
  if (secret !== null) {
    headers.set("x-telegram-bot-api-secret-token", secret);
  }
  return new Request("https://worker.example/telegram/webhook", {
    method: "POST",
    headers,
    body: JSON.stringify({
      update_id: updateId,
      message: {
        message_id: updateId + 100,
        from: { id: userId },
        chat: { id: chatId, type: options.chatType ?? "private" },
        ...(text === null ? {} : { text }),
      },
    }),
  });
}
