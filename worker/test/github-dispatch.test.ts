import { afterEach, describe, expect, it, vi } from "vitest";

import { GitHubWorkflowDispatcher } from "../src/dispatch/github";

describe("GitHub workflow dispatcher", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("posts only to the fixed repository workflow and never follows redirects", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await new GitHubWorkflowDispatcher("secret-token").dispatch(
      "paper-engine:2026-09-02T12:00:00.000Z",
    );

    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(
      "https://api.github.com/repos/Labhary/btc-sentinel/actions/workflows/paper-engine.yml/dispatches",
    );
    expect(init).toMatchObject({ method: "POST", redirect: "error" });
    expect(JSON.parse(String(init.body))).toEqual({
      ref: "main",
      inputs: { dispatch_key: "paper-engine:2026-09-02T12:00:00.000Z" },
    });
  });

  it("returns only a bounded status error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("secret body", { status: 403 })));
    await expect(new GitHubWorkflowDispatcher("secret-token").dispatch("safe-key")).rejects.toThrow(
      "GITHUB_DISPATCH_403",
    );
  });
});
