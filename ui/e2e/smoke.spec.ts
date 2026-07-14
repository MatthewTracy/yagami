import { expect, test } from "@playwright/test";

test("desktop control surface loads", async ({ page }) => {
  await page.route("**/api/sessions?limit=100", (route) =>
    route.fulfill({ json: { sessions: [] } }),
  );
  await page.route("**/api/costs", (route) =>
    route.fulfill({
      json: {
        today_usd: 0,
        session_usd: 0,
        daily_cap_usd: 5,
        cap_remaining_usd: 5,
        cap_exceeded: false,
      },
    }),
  );
  await page.addInitScript(() => {
    class MockWebSocket extends EventTarget {
      static readonly CONNECTING = 0;
      static readonly OPEN = 1;
      static readonly CLOSING = 2;
      static readonly CLOSED = 3;
      readonly CONNECTING = 0;
      readonly OPEN = 1;
      readonly CLOSING = 2;
      readonly CLOSED = 3;
      readyState = MockWebSocket.CONNECTING;
      onopen: ((event: Event) => void) | null = null;
      onclose: ((event: CloseEvent) => void) | null = null;
      onmessage: ((event: MessageEvent) => void) | null = null;

      constructor(_url: string | URL) {
        super();
        setTimeout(() => {
          this.readyState = MockWebSocket.OPEN;
          this.onopen?.(new Event("open"));
        }, 0);
      }

      send() {}

      close() {
        this.readyState = MockWebSocket.CLOSED;
      }
    }
    window.WebSocket = MockWebSocket as unknown as typeof WebSocket;
  });
  await page.goto("/");
  await expect(page.getByText("Yagami", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Settings" })).toBeVisible();
  await expect(page.locator("textarea")).toBeVisible();
});
