import type {
  BotStore,
  CommandAuditResult,
  DeliveryStatus,
  OutboxMessage,
  TelegramSender,
} from "../contracts";
import { TelegramRejectedError } from "../telegram/sender";

export interface DeliveryResult {
  status: DeliveryStatus;
  auditResult: CommandAuditResult;
}

function existingResult(status: DeliveryStatus): DeliveryResult {
  switch (status) {
    case "SENT":
      return { status, auditResult: "SENT" };
    case "FAILED":
      return { status, auditResult: "DELIVERY_FAILED" };
    case "UNKNOWN":
    case "SENDING":
      return { status: "UNKNOWN", auditResult: "DELIVERY_UNKNOWN" };
    case "PENDING":
      throw new Error("PENDING is not an existing terminal result");
  }
}

export async function deliverOnce(
  store: BotStore,
  sender: TelegramSender,
  message: OutboxMessage,
  now: string,
): Promise<DeliveryResult> {
  const status = await store.prepareOutbox(message);
  if (status !== "PENDING") {
    return existingResult(status);
  }

  // UNKNOWN is written before the external call. A crash after Telegram accepts
  // the message will therefore never cause a blind duplicate replay.
  await store.markOutboxUnknown(message.outboxId, now);
  try {
    const sent = await sender.sendMessage(message.chatId, message.text);
    await store.markOutboxSent(message.outboxId, sent.messageId, now);
    return { status: "SENT", auditResult: "SENT" };
  } catch (error) {
    if (error instanceof TelegramRejectedError) {
      await store.markOutboxFailed(message.outboxId, error.errorCode, now);
      return { status: "FAILED", auditResult: "DELIVERY_FAILED" };
    }
    return { status: "UNKNOWN", auditResult: "DELIVERY_UNKNOWN" };
  }
}
