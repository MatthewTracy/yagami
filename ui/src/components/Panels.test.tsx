import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryPanel } from "./MemoryPanel";
import { StatsDashboard } from "./StatsDashboard";
import { CostMeter } from "./CostMeter";
import { PrivacyLedger } from "./PrivacyLedger";
import { ToastHost } from "./Toast";
import { deferred, json } from "../test/fixtures";

const observation = {
  id: 1,
  session_id: "s1",
  role: "user",
  text: "stored observation",
  sensitivity: "none",
  created_at: 1000,
  embedding_status: "ready",
};
const stats = {
  window_days: 14,
  total_turns: 0,
  total_cost_usd: 0,
  by_backend: [],
  by_day: [],
  by_classification_source: [],
};

beforeEach(() =>
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url) => {
      if (String(url).includes("/memory/stats"))
        return json({ total: 1, vec_total: 1, by_status: {} });
      if (String(url).includes("/memory"))
        return json({ observations: [observation] });
      return json(stats);
    }),
  ),
);
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("panel failures, races, and privacy", () => {
  it.each(["memory", "statistics"])(
    "offers recovery after %s requests fail",
    async (panel) => {
      const normal = vi.mocked(fetch).getMockImplementation()!;
      vi.mocked(fetch).mockRejectedValue(new Error("offline"));
      if (panel === "memory") render(<MemoryPanel open onClose={() => {}} />);
      else render(<StatsDashboard open onClose={() => {}} />);
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "could not be loaded",
      );
      vi.mocked(fetch).mockImplementation(normal);
      await userEvent.click(screen.getByRole("button", { name: "Try again" }));
      if (panel === "memory")
        expect(await screen.findByText("stored observation")).toBeVisible();
      else expect(await screen.findByText(/No turns yet/)).toBeVisible();
    },
  );

  it("does not replace a newer statistics period with a slow old response", async () => {
    const old = deferred<Response>();
    vi.mocked(fetch)
      .mockImplementationOnce(() => old.promise)
      .mockResolvedValue(json({ ...stats, window_days: 7, total_turns: 23 }));
    render(<StatsDashboard open onClose={() => {}} />);
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Statistics period" }),
      "7",
    );
    expect(await screen.findByText("23")).toBeVisible();
    await act(async () => old.resolve(json({ ...stats, total_turns: 99 })));
    expect(screen.queryByText("99")).not.toBeInTheDocument();
    expect(screen.getByText("23")).toBeVisible();
  });

  it("encodes memory queries and ignores a stale search after All is selected", async () => {
    const user = userEvent.setup();
    const search = deferred<Response>();
    const normal = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation((url, init) =>
      String(url).includes("/search?") ? search.promise : normal(url, init),
    );
    render(<MemoryPanel open onClose={() => {}} />);
    await screen.findByText("stored observation");
    await user.type(
      screen.getByRole("textbox", { name: "Search memory" }),
      "a & b/日本語",
    );
    await user.click(screen.getByRole("button", { name: "Search" }));
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(([url]) => String(url).includes("q=a%20%26%20b%2F")),
    ).toBe(true);
    await user.click(screen.getByRole("button", { name: "All" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Search" })).toBeEnabled(),
    );
    await act(async () =>
      search.resolve(
        json({ observations: [{ ...observation, text: "stale search" }] }),
      ),
    );
    expect(screen.queryByText("stale search")).not.toBeInTheDocument();
    expect(screen.getByText("stored observation")).toBeVisible();
  });

  it("keeps memory visible when deletion fails and deletes only after success", async () => {
    render(
      <>
        <MemoryPanel open onClose={() => {}} />
        <ToastHost />
      </>,
    );
    await screen.findByText("stored observation");
    vi.mocked(fetch).mockRejectedValueOnce(new Error("offline"));
    await userEvent.click(
      screen.getByRole("button", { name: "Delete observation 1" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not delete",
    );
    expect(screen.getByText("stored observation")).toBeVisible();
    await userEvent.click(
      screen.getByRole("button", { name: "Delete observation 1" }),
    );
    await waitFor(() =>
      expect(screen.queryByText("stored observation")).not.toBeInTheDocument(),
    );
  });

  it.each([0, 4.5, 8])(
    "handles daily spend %s against a five-dollar limit",
    async (today) => {
      vi.mocked(fetch).mockResolvedValue(
        json({
          today_usd: today,
          session_usd: 0.002,
          daily_cap_usd: 5,
          cap_remaining_usd: Math.max(0, 5 - today),
          cap_exceeded: today >= 5,
        }),
      );
      render(<CostMeter sessionId={null} refreshKey={0} />);
      const progress = await screen.findByRole("progressbar", {
        name: "Daily budget used",
      });
      expect(progress).toHaveAttribute("value", String(Math.min(5, today)));
      expect(screen.getByText("$0.0020")).toBeVisible();
      if (today >= 5) expect(screen.getByText(/cloud blocked/)).toBeVisible();
    },
  );

  it("does not invent a zero spend when usage data is unavailable", async () => {
    vi.mocked(fetch).mockRejectedValue(new Error("offline"));
    render(<CostMeter sessionId={null} refreshKey={0} />);
    expect(await screen.findByText("Usage is unavailable.")).toBeVisible();
    expect(screen.queryByText("$0.0000")).not.toBeInTheDocument();
  });

  it.each(["phi", "phi_medical", "secret"])(
    "shows containment without promising cloud image access for %s",
    async (sensitivity) => {
      vi.mocked(fetch).mockResolvedValue(
        json({
          decisions: [
            {
              id: 1,
              session_id: "s1",
              created_at: 1000,
              backend: "ollama",
              is_local: true,
              reason: "sensitive",
              classification: { sensitivity },
              profile: null,
              t_classify_ms: null,
              t_first_token_ms: null,
              t_total_ms: null,
            },
          ],
        }),
      );
      const reset = vi.fn();
      window.addEventListener("yagami:reset-phi", reset);
      render(<PrivacyLedger sessionId="s1" refreshKey={0} />);
      expect(
        await screen.findByText("Session contains sensitive content"),
      ).toBeVisible();
      expect(
        screen.queryByText(/Image gen still works/),
      ).not.toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: "Reset" }));
      expect(reset).toHaveBeenCalledOnce();
      window.removeEventListener("yagami:reset-phi", reset);
    },
  );
  it("clears previous privacy decisions while the selected session loads", async () => {
    const old = deferred<Response>();
    const next = deferred<Response>();
    vi.mocked(fetch)
      .mockImplementationOnce(() => old.promise)
      .mockImplementationOnce(() => next.promise);
    const { rerender } = render(
      <PrivacyLedger sessionId="old & session" refreshKey={0} />,
    );
    expect(vi.mocked(fetch).mock.calls[0][0]).toContain("old%20%26%20session");
    rerender(<PrivacyLedger sessionId="new" refreshKey={0} />);
    await act(async () =>
      old.resolve(
        json({ decisions: [{ id: 1, reason: "old private route" }] }),
      ),
    );
    expect(screen.queryByText("old private route")).not.toBeInTheDocument();
    await act(async () => next.resolve(json({ decisions: [] })));
    expect(screen.getByText("No routing decisions yet.")).toBeVisible();
    rerender(<PrivacyLedger sessionId={null} refreshKey={0} />);
    expect(screen.getByText("No session yet.")).toBeVisible();
  });

  it("does not show the old session's spending while its replacement loads", async () => {
    const next = deferred<Response>();
    vi.mocked(fetch)
      .mockResolvedValueOnce(
        json({
          today_usd: 2,
          session_usd: 0.73,
          daily_cap_usd: 0,
          cap_exceeded: false,
        }),
      )
      .mockImplementationOnce(() => next.promise);
    const { rerender } = render(<CostMeter sessionId="old" refreshKey={0} />);
    expect(await screen.findByText("$0.73")).toBeVisible();
    rerender(<CostMeter sessionId="new" refreshKey={0} />);
    expect(screen.queryByText("$0.73")).not.toBeInTheDocument();
    expect(screen.getByText("Loading usage…")).toBeVisible();
    await act(async () =>
      next.resolve(
        json({
          today_usd: 2,
          session_usd: 0.24,
          daily_cap_usd: 0,
          cap_exceeded: false,
        }),
      ),
    );
    expect(screen.getByText("$0.24")).toBeVisible();
  });
});
