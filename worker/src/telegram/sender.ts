import type { SentTelegramMessage, TelegramSender } from "../contracts";

export class TelegramRejectedError extends Error {
  override readonly name = "TelegramRejectedError";

  constructor(readonly errorCode: string) {
    super("Telegram rejected the message");
  }
}

type FetchFunction = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export class TelegramApiSender implements TelegramSender {
  constructor(
    private readonly token: string,
    private readonly fetchFunction: FetchFunction = fetch,
    private readonly timeoutMilliseconds = 8_000,
  ) {}

  async sendMessage(chatId: string, text: string): Promise<SentTelegramMessage> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMilliseconds);
    let response: Response;
    try {
      response = await this.fetchFunction(`https://api.telegram.org/bot${this.token}/sendMessage`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ chat_id: chatId, text }),
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timeout);
    }

    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      throw new Error("Telegram returned an unreadable response");
    }
    if (isRecord(payload) && payload.ok === false) {
      const code =
        typeof payload.error_code === "number"
          ? `TELEGRAM_${payload.error_code}`
          : "TELEGRAM_REJECTED";
      throw new TelegramRejectedError(code);
    }
    if (!response.ok || !isRecord(payload) || payload.ok !== true || !isRecord(payload.result)) {
      throw new Error("Telegram delivery result is uncertain");
    }
    const messageId = payload.result.message_id;
    if (typeof messageId !== "number" || !Number.isSafeInteger(messageId)) {
      throw new Error("Telegram response did not contain a valid message ID");
    }
    return { messageId: String(messageId) };
  }
}
