import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { deferred, json } from "../test/fixtures";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

import { AssistantBubble } from "./AssistantBubble";

describe("AssistantBubble", () => {
  it("renders markdown without executing raw HTML", () => {
    const { container } = render(
      <AssistantBubble
        text={'**safe** <script data-testid="unsafe">alert(1)</script>'}
        pending={false}
        isLastAssistant={false}
      />,
    );

    expect(screen.getByText("safe")).toBeInTheDocument();
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByText(/<script/)).toBeInTheDocument();
  });

  it("renders tool and recall provenance", () => {
    render(
      <AssistantBubble
        text="answer"
        pending={false}
        isLastAssistant={false}
        toolCalls={[{ name: "calc.eval", ok: true, resultBytes: 1 }]}
        recall={[
          {
            id: 1,
            role: "user",
            text: "earlier context",
            session_id: "session-123",
            source: "memory",
            distance: 0.1,
          },
        ]}
      />,
    );

    expect(
      screen.getByText(/recalled 1 from prior session/),
    ).toBeInTheDocument();
    expect(screen.getByText(/calc\.eval/)).toBeInTheDocument();
  });
  it("serializes feedback, toggles it off, and restores the previous vote after failure", async () => {
    const user = userEvent.setup();
    const first = deferred<Response>();
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockImplementationOnce(() => first.promise)
        .mockResolvedValueOnce(json({}))
        .mockResolvedValueOnce(new Response(null, { status: 503 })),
    );
    render(
      <AssistantBubble
        text="answer"
        pending={false}
        isLastAssistant={false}
        decisionId={17}
      />,
    );
    const helpful = screen.getByRole("button", {
      name: "Helpful",
    });
    const unhelpful = screen.getByRole("button", {
      name: "Not helpful",
    });
    await user.click(helpful);
    expect(helpful).toHaveAttribute("aria-pressed", "true");
    expect(helpful).toBeDisabled();
    expect(unhelpful).toBeDisabled();
    await user.click(unhelpful);
    expect(fetch).toHaveBeenCalledTimes(1);
    await act(async () => first.resolve(json({})));
    await user.click(helpful);
    await waitFor(() => expect(helpful).toBeEnabled());
    expect(vi.mocked(fetch).mock.calls[1]).toEqual([
      "/api/decisions/17/feedback",
      { method: "DELETE" },
    ]);
    await user.click(unhelpful);
    await waitFor(() => expect(unhelpful).toBeEnabled());
    expect(unhelpful).toHaveAttribute("aria-pressed", "false");
    expect(helpful).toHaveAttribute("aria-pressed", "false");
  });

  it("restores an existing vote when the feedback network request fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(json({}))
        .mockRejectedValueOnce(new Error("offline")),
    );
    render(
      <AssistantBubble
        text="answer"
        pending={false}
        isLastAssistant={false}
        decisionId={7}
      />,
    );
    const helpful = screen.getByRole("button", {
      name: "Helpful",
    });
    await userEvent.click(helpful);
    await waitFor(() => expect(helpful).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: "Not helpful" }));
    await waitFor(() =>
      expect(helpful).toHaveAttribute("aria-pressed", "true"),
    );
  });

  it("copies the complete markdown and clears the confirmation timer on unmount", async () => {
    vi.useFakeTimers();
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    const { unmount } = render(
      <AssistantBubble
        text="**complete** answer"
        pending={false}
        isLastAssistant={false}
      />,
    );
    await act(async () => screen.getByRole("button", { name: "Copy" }).click());
    expect(writeText).toHaveBeenCalledWith("**complete** answer");
    expect(screen.getByRole("button", { name: "Copied" })).toBeInTheDocument();
    await act(async () => vi.advanceTimersByTime(1500));
    expect(screen.getByRole("button", { name: "Copy" })).toBeInTheDocument();
    await act(async () => screen.getByRole("button", { name: "Copy" }).click());
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("keeps the response usable when clipboard permission is denied", async () => {
    vi.stubGlobal("navigator", {
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
    });
    render(
      <AssistantBubble text="answer" pending={false} isLastAssistant={false} />,
    );
    await act(async () => screen.getByRole("button", { name: "Copy" }).click());
    expect(screen.getByRole("button", { name: "Copy" })).toBeEnabled();
  });

  it("expands recall and tool evidence without injecting stored HTML", async () => {
    render(
      <AssistantBubble
        text="answer"
        pending={false}
        isLastAssistant={false}
        toolCalls={[
          {
            name: "web.fetch",
            ok: false,
            errorCode: "refused",
            artifacts: { url: "https://example.com" },
          },
        ]}
        recall={[
          {
            id: 1,
            text: "<script>private</script>",
            role: "user",
            session_id: "previous-session",
            source: "memory",
            distance: null,
          },
          {
            id: 2,
            text: "related context",
            role: "assistant",
            session_id: "previous-session",
            source: "memory",
            distance: 0.125,
          },
        ]}
      />,
    );
    const recall = screen.getByRole("button", { name: /recalled 2/ });
    expect(recall).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(recall);
    expect(screen.getByText("<script>private</script>")).toBeVisible();
    expect(screen.getByText(/d=0.125/)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: /web.fetch/ }));
    expect(screen.getByText(/Content-free evidence/)).toBeVisible();
    expect(screen.getByText(/"url":/)).toHaveTextContent("https://example.com");
    await userEvent.click(recall);
    expect(screen.queryByText("related context")).not.toBeInTheDocument();
  });

  it("hides completed-response actions while streaming and regenerates only the last response", async () => {
    const regenerate = vi.fn();
    const { rerender } = render(
      <AssistantBubble
        text=""
        pending
        pendingHint="Loading local model"
        isLastAssistant
        onRegenerate={regenerate}
      />,
    );
    expect(screen.getByText("Loading local model")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Regenerate" }),
    ).not.toBeInTheDocument();
    rerender(
      <AssistantBubble
        text="partial"
        pending
        isLastAssistant
        onRegenerate={regenerate}
      />,
    );
    expect(
      screen.queryByRole("button", { name: "Copy" }),
    ).not.toBeInTheDocument();
    rerender(
      <AssistantBubble
        text="done"
        image="data:image/png;base64,AA=="
        pending={false}
        isLastAssistant
        onRegenerate={regenerate}
      />,
    );
    expect(screen.getByRole("img", { name: "generated" })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Regenerate" }));
    expect(regenerate).toHaveBeenCalledOnce();
    rerender(
      <AssistantBubble
        text="older"
        pending={false}
        isLastAssistant={false}
        onRegenerate={regenerate}
      />,
    );
    expect(
      screen.queryByRole("button", { name: "Regenerate" }),
    ).not.toBeInTheDocument();
  });
});
