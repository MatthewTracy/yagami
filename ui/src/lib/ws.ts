export type ServerMsg =
  | { type: "session"; session_id: string }
  | {
      type: "routing";
      backend: string;
      is_local: boolean;
      reason: string;
      classification: Record<string, unknown>;
      decision_id?: number;
    }
  | { type: "text"; content: string; meta: Record<string, unknown> }
  | { type: "image_url"; content: string; meta: Record<string, unknown> }
  | { type: "error"; content: string; meta: Record<string, unknown> }
  | { type: "done"; content: string; meta: Record<string, unknown> };

export type ClientImage = { media_type: string; data_b64: string };

export type ClientMsg =
  | { content: string; force_backend?: string | null; images?: ClientImage[] }
  | { type: "cancel" }
  | { type: "load_session"; session_id: string };

export function connectChat(onMsg: (m: ServerMsg) => void, onClose?: () => void, onOpen?: () => void): WebSocket {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${window.location.host}/ws/chat`);
  ws.onmessage = (e) => onMsg(JSON.parse(e.data) as ServerMsg);
  ws.onclose = () => onClose?.();
  ws.onopen = () => onOpen?.();
  return ws;
}

export function sendChat(ws: WebSocket, msg: ClientMsg): void {
  if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
}
