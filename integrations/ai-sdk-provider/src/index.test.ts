import { embed, generateText } from "ai";
import { describe, expect, it, vi } from "vitest";

import { createYagami } from "./index.js";

describe("Yagami AI SDK provider", () => {
  it("returns content-free governance metadata", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "chatcmpl-test",
          model: "ollama",
          choices: [
            {
              finish_reason: "stop",
              message: { role: "assistant", content: "safe response" },
            },
          ],
          usage: {
            prompt_tokens: 2,
            completion_tokens: 2,
            total_tokens: 4,
          },
        }),
        {
          status: 200,
          headers: {
            "content-type": "application/json",
            "x-yagami-request-id": "ygm_test",
            "x-yagami-decision-id": "42",
            "x-yagami-backend": "ollama",
            "x-yagami-policy-hash": "sha256:test",
          },
        },
      ),
    );
    const provider = createYagami({
      baseURL: "http://yagami.test/v1",
      apiKey: "test-key",
      fetch: fetcher,
    });
    const result = await generateText({
      model: provider(),
      prompt: "private prompt",
    });
    expect(result.text).toBe("safe response");
    expect(result.providerMetadata?.yagami).toEqual({
      requestId: "ygm_test",
      decisionId: "42",
      backend: "ollama",
      policyHash: "sha256:test",
    });
    const request = fetcher.mock.calls[0]?.[1];
    expect(new Headers(request?.headers).get("authorization")).toBe("Bearer test-key");
    expect(JSON.stringify(result.providerMetadata)).not.toContain("private prompt");
  });

  it("supports governed embeddings", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          object: "list",
          model: "test-embedding",
          data: [{ object: "embedding", index: 0, embedding: [0.1, 0.2] }],
          usage: { total_tokens: 2, prompt_tokens: 2 },
        }),
        {
          status: 200,
          headers: {
            "content-type": "application/json",
            "x-yagami-decision-id": "43",
            "x-yagami-trust-zone": "device",
          },
        },
      ),
    );
    const provider = createYagami({ fetch: fetcher });
    const result = await embed({
      model: provider.embedding(),
      value: "private retrieval content",
    });
    expect(result.embedding).toEqual([0.1, 0.2]);
    expect(result.providerMetadata?.yagami).toEqual({
      decisionId: "43",
      trustZone: "device",
    });
  });
});

