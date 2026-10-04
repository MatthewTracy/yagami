import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Chat } from "./Chat";
import { ToastHost } from "./Toast";
import { ChatSocket } from "../test/chatSocket";
import { deferred, json } from "../test/fixtures";

function setup(loadSessionId: string | null = null, open = true) {
  const callbacks = {
    onRouting: vi.fn(),
    onSession: vi.fn(),
    onTurnComplete: vi.fn(),
  };
  const view = render(
    <>
      <Chat {...callbacks} loadSessionId={loadSessionId} />
      <ToastHost />
    </>,
  );
  const socket = ChatSocket.instances.at(-1)!;
  if (open) act(() => socket.open());
  return {
    ...view,
    socket,
    callbacks,
    user: userEvent.setup(),
    input: screen.getByRole("textbox", { name: "Message Yagami" }),
  };
}

function finish(socket: ChatSocket, content = "A useful answer") {
  act(() => {
    socket.receive({
      type: "routing",
      backend: "ollama",
      is_local: true,
      reason: "local policy",
      classification: { sensitivity: "none" },
    });
    socket.receive({ type: "text", content, meta: {} });
    socket.receive({ type: "done", content: "", meta: {} });
  });
}

beforeEach(() => {
  sessionStorage.clear();
  ChatSocket.instances = [];
  vi.stubGlobal("WebSocket", ChatSocket);
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => json({ messages: [] })),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

describe("chat edge cases", () => {
  it("does not send empty/whitespace messages or submit during IME composition", async () => {
    const { socket, input, user } = setup();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    await user.type(input, "   ");
    await user.keyboard("{Enter}");
    expect(socket.sent).toEqual([]);
    await user.clear(input);
    await user.type(input, "日本語");
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    expect(socket.sent).toEqual([]);
    await user.keyboard("{Shift>}{Enter}{/Shift}");
    expect(input).toHaveValue("日本語\n");
    await user.keyboard("{Enter}");
    expect(socket.sent).toEqual([{ content: "日本語" }]);
  });

  it("streams a response, notifies callbacks, and prevents duplicate sends", async () => {
    const { socket, input, user, callbacks } = setup();
    act(() => socket.receive({ type: "session", session_id: "session-1" }));
    await user.type(input, "hello");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(input).toBeDisabled();
    expect(sessionStorage.getItem("yagami:draft")).toBeNull();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(socket.sent).toHaveLength(1);
    finish(socket);
    expect(screen.getByText("A useful answer")).toBeVisible();
    expect(callbacks.onSession).toHaveBeenCalledWith("session-1");
    expect(callbacks.onRouting).toHaveBeenCalledWith(
      expect.objectContaining({ backend: "ollama", isLocal: true }),
    );
    expect(callbacks.onTurnComplete).toHaveBeenCalledOnce();
    expect(input).toBeEnabled();
  });

  it("a send failure preserves the draft and does not invent a sent message", async () => {
    const { socket, input, user } = setup();
    socket.send.mockImplementation(() => {
      throw new Error("transport failure");
    });
    await user.type(input, "keep this private draft");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(input).toHaveValue("keep this private draft");
    expect(screen.getByRole("alert")).toHaveTextContent("draft has been kept");
    expect(screen.getByText(/Your context/)).toBeVisible();
    expect(input).toBeEnabled();
  });

  it("disconnects during generation, reconnects to the same session, and never replays the prompt", async () => {
    const { socket, input, user } = setup();
    act(() => socket.receive({ type: "session", session_id: "saved-session" }));
    await user.type(input, "hello");
    await user.click(screen.getByRole("button", { name: "Send" }));
    act(() => socket.close());
    expect(
      screen.queryByRole("button", { name: "Stop" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Disconnected from gateway")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Reconnect" }));
    const replacement = ChatSocket.instances.at(-1)!;
    act(() => replacement.open());
    expect(replacement.sent).toEqual([
      { type: "load_session", session_id: "saved-session" },
    ]);
    expect(input).toBeEnabled();
    act(() =>
      socket.receive({ type: "text", content: "stale response", meta: {} }),
    );
    expect(screen.queryByText("stale response")).not.toBeInTheDocument();
  });

  it("keeps an unsent draft through a disconnect", async () => {
    const { socket, input, user } = setup();
    await user.type(input, "unsent draft");
    act(() => socket.close());
    expect(input).toHaveValue("unsent draft");
    expect(sessionStorage.getItem("yagami:draft")).toBe("unsent draft");
  });

  it("unblocks the composer on a standalone server error", async () => {
    const { socket, input, user } = setup();
    await user.type(input, "hello");
    await user.click(screen.getByRole("button", { name: "Send" }));
    act(() =>
      socket.receive({
        type: "error",
        content: "Policy denied this turn",
        meta: {},
      }),
    );
    expect(input).toBeEnabled();
    expect(screen.getByRole("alert")).toHaveTextContent("Policy denied");
  });

  it("cancels via Stop and regenerates only after a completed turn", async () => {
    const { socket, input, user } = setup();
    await user.type(input, "repeat me");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await user.click(screen.getByRole("button", { name: "Stop" }));
    expect(socket.sent.at(-1)).toEqual({ type: "cancel" });
    finish(socket);
    await user.click(screen.getByTitle("Regenerate"));
    expect(socket.sent.at(-1)).toEqual({ content: "repeat me" });
    expect(socket.sent).toHaveLength(3);
    expect(screen.queryByText("A useful answer")).not.toBeInTheDocument();
  });

  it("waits for the socket before loading a selected session", async () => {
    vi.mocked(fetch).mockResolvedValue(
      json({
        messages: [
          { role: "user", content: "saved question" },
          { role: "assistant", content: "saved answer" },
        ],
      }),
    );
    const { socket } = setup("selected-session", false);
    expect(fetch).not.toHaveBeenCalled();
    act(() => socket.open());
    expect(await screen.findByText("saved answer")).toBeVisible();
    expect(socket.sent).toEqual([
      { type: "load_session", session_id: "selected-session" },
    ]);
  });

  it("ignores a late session response after selection changes", async () => {
    const old = deferred<Response>();
    vi.mocked(fetch).mockImplementation(async (url) =>
      String(url).includes("first")
        ? old.promise
        : json({ messages: [{ role: "assistant", content: "new history" }] }),
    );
    const { rerender, callbacks } = setup("first");
    rerender(<Chat {...callbacks} loadSessionId="second" />);
    expect(await screen.findByText("new history")).toBeVisible();
    await act(async () =>
      old.resolve(
        json({ messages: [{ role: "assistant", content: "old history" }] }),
      ),
    );
    expect(screen.queryByText("old history")).not.toBeInTheDocument();
  });

  it("blocks sends while ingestion is pending, then includes the complete document", async () => {
    const ingestion = deferred<Response>();
    vi.mocked(fetch).mockImplementation(async () => ingestion.promise);
    const { socket, input, user, container } = setup();
    await user.type(input, "review this");
    await user.upload(
      container.querySelector<HTMLInputElement>('input[type="file"]')!,
      new File(["body"], "notes.txt", { type: "text/plain" }),
    );
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    await user.keyboard("{Enter}");
    expect(socket.sent).toEqual([]);
    await act(async () =>
      ingestion.resolve(
        json({
          kind: "document",
          filename: "notes.txt",
          text: "complete document",
          chars: 17,
          truncated: false,
        }),
      ),
    );
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(socket.sent).toEqual([
      expect.objectContaining({
        content: expect.stringContaining("complete document"),
      }),
    ]);
  });

  it("surfaces upload errors and supports removing an image attachment", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(json({ detail: "too large" }, 413))
      .mockResolvedValueOnce(
        json({
          kind: "image",
          filename: "photo.png",
          media_type: "image/png",
          data_b64: "aGVsbG8=",
        }),
      );
    const { user, container } = setup();
    const picker =
      container.querySelector<HTMLInputElement>('input[type="file"]')!;
    await user.upload(
      picker,
      new File(["bad"], "large.txt", { type: "text/plain" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("Upload failed");
    await user.upload(
      picker,
      new File(["image"], "photo.png", { type: "image/png" }),
    );
    await user.click(
      await screen.findByRole("button", { name: "Remove photo.png" }),
    );
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });

  it("fills a starter prompt without automatically sending it", async () => {
    const { socket, user, input } = setup();
    await user.click(screen.getByRole("button", { name: /Explore a topic/ }));
    expect((input as HTMLTextAreaElement).value).toContain("context firewall");
    expect(input).toHaveFocus();
    expect(socket.sent).toEqual([]);
    await waitFor(() =>
      expect(sessionStorage.getItem("yagami:draft")).toContain(
        "context firewall",
      ),
    );
  });
});
