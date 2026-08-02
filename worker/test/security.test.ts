import { describe, expect, it } from "vitest";

import { secureEquals } from "../src/security";

describe("secureEquals", () => {
  it("accepts only the exact value", async () => {
    await expect(secureEquals("correct-value", "correct-value")).resolves.toBe(true);
    await expect(secureEquals("correct-valuE", "correct-value")).resolves.toBe(false);
    await expect(secureEquals(null, "correct-value")).resolves.toBe(false);
  });
});
