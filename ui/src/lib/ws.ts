export type ServerMsg =
  | { type: "session"; session_id: string }
  | { type: "routing"; backend: string; is_local: boolean; reason: string; classification: Record<string, unknown> }
  | { type: "text"; content: string; meta: Record<string, unknown> }
  | { type: "image_url"; content: string; meta: Record<string, unknown> }
  | { type: "error"; content: string; meta: Record<string, unknown> }
  | { type: "done"; content: string; meta: Record<string, unknown> };

export function connectChat(onMsg: (m: ServerMsg) => void, onClose?: () => void): WebSocket {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${window.location.host}/ws/chat`);
  ws.onmessage = (e) => onMsg(JSON.parse(e.data) as ServerMsg);
  ws.onclose = () => onClose?.();
  return ws;
}
