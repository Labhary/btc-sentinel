import { afterEach, describe, expect, it, vi } from "vitest";

import { TelegramApiSender, TelegramRejectedError } from "../src/telegram/sender";

const token = "123456789" + ":" + "A".repeat(32);

afterEach(() => {
  vi.restoreAllMocks();
});

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
    vi.spyOn(console, "error").mockImplementation(() => undefined);
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

  it("logs only a safe category for fetch failures", async () => {
    const log = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const sender = new TelegramApiSender(token, async () => {
      throw new Error(`connection failed for ${token} and chat 424242`);
    });

    await expect(sender.sendMessage("424242", "secret message text")).rejects.toThrow();

    expect(log).toHaveBeenCalledWith("telegram_delivery_diagnostic category=FETCH_ERROR_OTHER");
    const logged = log.mock.calls.flat().join(" ");
    expect(logged).not.toContain(token);
    expect(logged).not.toContain("424242");
    expect(logged).not.toContain("secret message text");
    expect(logged).not.toContain("connection failed");
  });

  it("logs safe response categories with status only", async () => {
    const log = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const unreadable = new TelegramApiSender(
      token,
      async () => new Response("upstream body must stay private", { status: 502 }),
    );

    await expect(unreadable.sendMessage("424242", "hello")).rejects.toThrow("unreadable response");

    expect(log).toHaveBeenCalledWith(
      "telegram_delivery_diagnostic category=UNREADABLE_RESPONSE status=502",
    );
    expect(log.mock.calls.flat().join(" ")).not.toContain("upstream body must stay private");
  });
});
