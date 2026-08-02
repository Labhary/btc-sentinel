import type { CommandName } from "../contracts";

export interface TelegramMessage {
  messageId: number;
  fromId: string | null;
  chatId: string;
  chatType: string;
  text: string | null;
}

export interface TelegramUpdate {
  updateId: number;
  message: TelegramMessage | null;
}

export interface ParsedCommand {
  name: CommandName;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function safeInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) ? value : null;
}

export function parseTelegramUpdate(value: unknown): TelegramUpdate | null {
  if (!isRecord(value)) {
    return null;
  }
  const updateId = safeInteger(value.update_id);
  if (updateId === null || updateId < 0) {
    return null;
  }
  if (!isRecord(value.message)) {
    return { updateId, message: null };
  }

  const messageId = safeInteger(value.message.message_id);
  const chat = value.message.chat;
  if (messageId === null || !isRecord(chat)) {
    return { updateId, message: null };
  }
  const chatId = safeInteger(chat.id);
  if (chatId === null || typeof chat.type !== "string") {
    return { updateId, message: null };
  }
  const fromId = isRecord(value.message.from) ? safeInteger(value.message.from.id) : null;
  const text = typeof value.message.text === "string" ? value.message.text : null;

  return {
    updateId,
    message: {
      messageId,
      fromId: fromId === null ? null : String(fromId),
      chatId: String(chatId),
      chatType: chat.type,
      text,
    },
  };
}

const COMMANDS: Record<string, CommandName> = {
  start: "START",
  help: "HELP",
  status: "STATUS",
  pause: "PAUSE",
  resume: "RESUME",
};

export function parseCommand(text: string | null): ParsedCommand | null {
  if (text === null || text.length > 256) {
    return null;
  }
  const match = /^\/([A-Za-z]+)(?:@[A-Za-z0-9_]{5,32})?\s*$/.exec(text.trim());
  if (!match) {
    return null;
  }
  const rawName = match[1]?.toLowerCase();
  if (!rawName) {
    return null;
  }
  return { name: COMMANDS[rawName] ?? "UNKNOWN" };
}

export function isAuthorizedPrivateOwner(message: TelegramMessage, adminUserId: string): boolean {
  return (
    message.chatType === "private" &&
    message.fromId === adminUserId &&
    message.chatId === adminUserId
  );
}
