import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";

class FakeWebSocket {
  static readonly OPEN = 1;
  readonly readyState = FakeWebSocket.OPEN;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onopen: (() => void) | null = null;

  constructor() {
    setTimeout(() => this.onopen?.(), 0);
  }

  send(_payload: string) {}
  close() {
    this.onclose?.();
  }
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("App", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.startsWith("/api/sessions")) return json({ sessions: [] });
        if (url.startsWith("/api/costs")) {
          return json({
            today_usd: 0,
            session_usd: 0,
            daily_cap_usd: 0,
            cap_remaining_usd: null,
            cap_exceeded: false,
          });
        }
        if (url.startsWith("/api/stats")) {
          return json({
            window_days: 7,
            total_turns: 0,
            total_cost_usd: 0,
            by_backend: [],
            by_day: [],
            by_classification_source: [],
          });
        }
        if (url === "/api/memory?limit=100") return json({ observations: [], count: 0 });
        if (url === "/api/memory/stats") {
          return json({ total: 0, vec_total: 0, by_status: {} });
        }
        return json({});
      }),
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    sessionStorage.clear();
  });

  it("loads the control surface and keeps drafts only in session storage", async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(screen.getByText("Yagami", { exact: true })).toBeInTheDocument();
    const textarea = await screen.findByPlaceholderText(/Message Yagami/);
    await user.type(textarea, "private draft");

    await waitFor(() => expect(sessionStorage.getItem("yagami:draft")).toBe("private draft"));
    expect(localStorage.getItem("yagami:draft")).toBeNull();
  });

  it("opens memory and stats panels with bounded empty states", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Cross-session memory" }));
    expect(await screen.findByText("Cross-session memory", { selector: "h3" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Close" }));

    await user.click(screen.getByRole("button", { name: "Stats dashboard" }));
    expect(await screen.findByText("Stats", { selector: "h3" })).toBeVisible();
  });
});
