import { describe, expect, it, vi } from "vitest";

import { connectChat, sendChat } from "./ws";

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
      new MessageEvent("message", { data: JSON.stringify({ type: "done", content: "", meta: {} }) }),
    );
    sendChat(socket as unknown as WebSocket, { content: "hello" });

    expect(socket.url).toBe("ws://localhost:3000/ws/chat");
    expect(onMessage).toHaveBeenCalledWith({ type: "done", content: "", meta: {} });
    expect(socket.send).toHaveBeenCalledWith(JSON.stringify({ content: "hello" }));
    vi.unstubAllGlobals();
  });
});
