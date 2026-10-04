import { test as base } from "@playwright/test";
import { settingsFixture } from "../src/test/fixtures";

declare global {
  interface Window {
    yagamiTest: {
      sent: unknown[];
      receive: (payload: unknown) => void;
      disconnect: () => void;
    };
  }
}

export const test = base.extend({
  page: async ({ page }, use) => {
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      let body: unknown = {};
      if (url.pathname === "/api/health")
        body = { ok: true, demo_mode: false, default_backend: "ollama" };
      else if (url.pathname === "/api/config") body = settingsFixture();
      else if (url.pathname === "/api/sessions") body = { sessions: [] };
      else if (url.pathname.startsWith("/api/sessions/"))
        body = {
          messages: [
            { role: "user", content: "Saved conversation" },
            { role: "assistant", content: "Saved response" },
          ],
        };
      else if (url.pathname === "/api/costs")
        body = {
          today_usd: 0,
          session_usd: 0,
          daily_cap_usd: 5,
          cap_remaining_usd: 5,
          cap_exceeded: false,
        };
      else if (url.pathname === "/api/decisions") body = { decisions: [] };
      else if (url.pathname === "/api/memory")
        body = { observations: [], count: 0 };
      else if (url.pathname === "/api/memory/stats")
        body = { total: 0, vec_total: 0, by_status: {} };
      else if (url.pathname === "/api/stats")
        body = {
          window_days: 14,
          total_turns: 0,
          total_cost_usd: 0,
          by_backend: [],
          by_day: [],
          by_classification_source: [],
        };
      else if (url.pathname === "/api/ingest")
        body = {
          kind: "document",
          filename: "notes.txt",
          text: "Document body",
          chars: 13,
          truncated: false,
        };
      await route.fulfill({ json: body });
    });
    await page.addInitScript(() => {
      class MockSocket extends EventTarget {
        static readonly OPEN = 1;
        static readonly CONNECTING = 0;
        static readonly CLOSED = 3;
        readyState = MockSocket.CONNECTING;
        onopen: ((event: Event) => void) | null = null;
        onclose: ((event: Event) => void) | null = null;
        onmessage: ((event: MessageEvent) => void) | null = null;
        sent: unknown[] = [];
        constructor() {
          super();
          window.yagamiTest = {
            sent: this.sent,
            receive: (payload) =>
              this.onmessage?.(
                new MessageEvent("message", { data: JSON.stringify(payload) }),
              ),
            disconnect: () => this.close(),
          };
          setTimeout(() => {
            if (this.readyState === MockSocket.CLOSED) return;
            this.readyState = MockSocket.OPEN;
            this.onopen?.(new Event("open"));
            this.onmessage?.(
              new MessageEvent("message", {
                data: JSON.stringify({
                  type: "session",
                  session_id: "test-session",
                }),
              }),
            );
          }, 0);
        }
        send(payload: string) {
          this.sent.push(JSON.parse(payload));
        }
        close() {
          this.readyState = MockSocket.CLOSED;
          this.onclose?.(new Event("close"));
        }
      }
      window.WebSocket = MockSocket as unknown as typeof WebSocket;
    });
    await use(page);
  },
});
export { expect } from "@playwright/test";
