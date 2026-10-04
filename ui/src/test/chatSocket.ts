import { vi } from "vitest";

export class ChatSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 3;
  static instances: ChatSocket[] = [];
  readyState = ChatSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  sent: unknown[] = [];
  send = vi.fn((payload: string) => {
    this.sent.push(JSON.parse(payload));
  });
  constructor() {
    ChatSocket.instances.push(this);
  }
  open() {
    this.readyState = ChatSocket.OPEN;
    this.onopen?.();
  }
  close() {
    this.readyState = ChatSocket.CLOSED;
    this.onclose?.();
  }
  receive(payload: unknown) {
    this.onmessage?.(
      new MessageEvent("message", { data: JSON.stringify(payload) }),
    );
  }
}
