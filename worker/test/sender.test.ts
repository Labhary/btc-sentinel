import { describe, expect, it } from "vitest";

import { TelegramApiSender, TelegramRejectedError } from "../src/telegram/sender";

const token = "123456789" + ":" + "A".repeat(32);

describe("TelegramApiSender", () => {
  it("sends a JSON request and returns Telegram's message ID", async () => {
    let capturedUrl = "";
    let capturedInit: RequestInit | undefined;
    const sender = new TelegramApiSender(token, async (input, init) => {
      capturedUrl = String(input);
      capturedInit = init;
      return Response.json({ ok: true, result: { message_id: 77 } });
    });

    await expect(sender.sendMessage("424242", "hello")).resolves.toEqual({
      messageId: "77",
    });
    expect(capturedUrl).toBe(`https://api.telegram.org/bot${token}/sendMessage`);
    expect(capturedInit?.method).toBe("POST");
    expect(JSON.parse(String(capturedInit?.body))).toEqual({
      chat_id: "424242",
      text: "hello",
    });
  });

  it("classifies an explicit Telegram rejection", async () => {
    const sender = new TelegramApiSender(token, async () =>
      Response.json({ ok: false, error_code: 403, description: "forbidden" }, { status: 403 }),
    );

    const error = await sender.sendMessage("424242", "hello").catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(TelegramRejectedError);
    expect((error as TelegramRejectedError).errorCode).toBe("TELEGRAM_403");
    expect(String(error)).not.toContain(token);
  });

  it("treats malformed or contradictory responses as uncertain", async () => {
    const malformed = new TelegramApiSender(
      token,
      async () => new Response("not-json", { status: 502 }),
    );
    const missingId = new TelegramApiSender(token, async () =>
      Response.json({ ok: true, result: {} }),
    );

    await expect(malformed.sendMessage("424242", "hello")).rejects.toThrow("unreadable response");
    await expect(missingId.sendMessage("424242", "hello")).rejects.toThrow("valid message ID");
  });
});
