import { describe, expect, it } from "vitest";

import {
  isAuthorizedPrivateOwner,
  parseCommand,
  parseTelegramUpdate,
} from "../src/telegram/update";

describe("Telegram update parsing", () => {
  it("parses a private text message without trusting extra fields", () => {
    expect(
      parseTelegramUpdate({
        update_id: 55,
        message: {
          message_id: 7,
          from: { id: 424242 },
          chat: { id: 424242, type: "private" },
          text: "/status",
          ignored: { arbitrary: true },
        },
      }),
    ).toEqual({
      updateId: 55,
      message: {
        messageId: 7,
        fromId: "424242",
        chatId: "424242",
        chatType: "private",
        text: "/status",
      },
    });
  });

  it("rejects unsafe or malformed update IDs", () => {
    expect(parseTelegramUpdate(null)).toBeNull();
    expect(parseTelegramUpdate({ update_id: -1 })).toBeNull();
    expect(parseTelegramUpdate({ update_id: Number.MAX_SAFE_INTEGER + 1 })).toBeNull();
  });

  it("normalizes supported bot commands", () => {
    expect(parseCommand(" /STATUS ")).toEqual({ name: "STATUS" });
    expect(parseCommand("/pause@My_Bot")).toEqual({ name: "PAUSE" });
    expect(parseCommand("/newthing")).toEqual({ name: "UNKNOWN" });
    expect(parseCommand("status")).toBeNull();
    expect(parseCommand("/status extra")).toBeNull();
  });

  it("requires the owner, their private chat, and matching IDs", () => {
    const message = {
      messageId: 1,
      fromId: "424242",
      chatId: "424242",
      chatType: "private",
      text: "/status",
    };

    expect(isAuthorizedPrivateOwner(message, "424242")).toBe(true);
    expect(isAuthorizedPrivateOwner({ ...message, chatType: "group" }, "424242")).toBe(false);
    expect(isAuthorizedPrivateOwner({ ...message, chatId: "111" }, "424242")).toBe(false);
    expect(isAuthorizedPrivateOwner(message, "111")).toBe(false);
  });
});
