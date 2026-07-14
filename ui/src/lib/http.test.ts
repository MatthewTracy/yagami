import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchJson } from "./http";

describe("fetchJson", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("returns typed JSON for successful responses", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    );

    await expect(fetchJson<{ ok: boolean }>("/health")).resolves.toEqual({ ok: true });
  });

  it("rejects failed and non-JSON responses", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response("no", { status: 503 }))
      .mockResolvedValueOnce(
        new Response("<html></html>", {
          status: 200,
          headers: { "content-type": "text/html" },
        }),
      );
    vi.stubGlobal("fetch", fetch);

    await expect(fetchJson("/failed")).rejects.toThrow("503");
    await expect(fetchJson("/html")).rejects.toThrow("non-JSON");
  });
});
