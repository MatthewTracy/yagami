import { afterEach, describe, expect, it, vi } from "vitest";

import { connectChat, sendChat } from "./ws";

afterEach(() => vi.unstubAllGlobals());

describe("chat WebSocket", () => {
  it("connects to the current host, parses messages, and serializes sends", () => {
    const sockets: MockSocket[] = [];
    class MockSocket {
      static readonly OPEN = 1;
      readonly readyState = MockSocket.OPEN;
      onmessage: ((event: MessageEvent) => void) | null = null;
      onclose: (() => void) | null = null;
      onopen: (() => void) | null = null;
      send = vi.fn();
      constructor(readonly url: string) {
        sockets.push(this);
      }
    }
    vi.stubGlobal("WebSocket", MockSocket);
    const onMessage = vi.fn();

    const socket = connectChat(onMessage) as unknown as MockSocket;
    socket.onmessage?.(
      new MessageEvent("message", {
        data: JSON.stringify({ type: "done", content: "", meta: {} }),
      }),
    );
    sendChat(socket as unknown as WebSocket, { content: "hello" });

    expect(socket.url).toBe("ws://localhost:3000/ws/chat");
    expect(onMessage).toHaveBeenCalledWith({
      type: "done",
      content: "",
      meta: {},
    });
    expect(socket.send).toHaveBeenCalledWith(
      JSON.stringify({ content: "hello" }),
    );
    vi.unstubAllGlobals();
  });
});

describe("WebSocket failures", () => {
  class Socket {
    static readonly OPEN = 1;
    readyState = 1;
    onmessage: ((event: MessageEvent) => void) | null = null;
    onclose: (() => void) | null = null;
    onopen: (() => void) | null = null;
    send = vi.fn();
  }

  it.each([
    "not json",
    "null",
    "42",
    "[]",
    '{"type":"unknown"}',
    '{"type":"recall","content":"","meta":{"hits":[{"id":1,"text":"x","role":"user","session_id":"s","source":"chat","distance":"oops"}]}}',
    '{"type":"text","content":null,"meta":{}}',
    '{"type":"routing","backend":1}',
    '{"type":"recall","content":"","meta":{"hits":[null]}}',
    '{"type":"tool_call","content":"","meta":{}}',
  ])("handles malformed frame %s without throwing", (data) => {
    vi.stubGlobal("WebSocket", Socket);
    const receive = vi.fn();
    const socket = connectChat(receive) as unknown as Socket;
    expect(() =>
      socket.onmessage?.(new MessageEvent("message", { data })),
    ).not.toThrow();
    expect(receive).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "error",
        meta: { code: "invalid_frame" },
      }),
    );
  });

  it.each([0, 2, 3])("does not queue sends in socket state %s", (state) => {
    vi.stubGlobal("WebSocket", Socket);
    const socket = new Socket();
    socket.readyState = state;
    expect(
      sendChat(socket as unknown as WebSocket, { content: "private draft" }),
    ).toBe(false);
    expect(socket.send).not.toHaveBeenCalled();
  });

  it("reports failure if send throws and invokes open/close callbacks", () => {
    vi.stubGlobal("WebSocket", Socket);
    const opened = vi.fn();
    const closed = vi.fn();
    const socket = connectChat(vi.fn(), closed, opened) as unknown as Socket;
    socket.onopen?.();
    socket.onclose?.();
    expect(opened).toHaveBeenCalledOnce();
    expect(closed).toHaveBeenCalledOnce();
    socket.send.mockImplementation(() => {
      throw new Error("closed during send");
    });
    expect(sendChat(socket as unknown as WebSocket, { content: "draft" })).toBe(
      false,
    );
  });
});
