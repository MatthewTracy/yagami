export type ServerMsg =
  | { type: "session"; session_id: string }
  | {
      type: "status";
      content: string;
      meta: {
        phase: "classifying" | "loading_model" | "generating";
        backend?: string;
      };
    }
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
  | {
      type: "tool_call";
      content: string;
      meta: {
        name: string;
        ok: boolean;
        error_code?: string | null;
        result_bytes?: number;
        artifacts?: Record<string, unknown>;
      };
    }
  | { type: "error"; content: string; meta: Record<string, unknown> }
  | {
      type: "recall";
      content: string;
      meta: {
        hits: {
          id: number;
          role: string;
          text: string;
          session_id: string;
          source: string;
          distance: number | null;
        }[];
      };
    }
  | { type: "done"; content: string; meta: Record<string, unknown> };

export type ClientImage = { media_type: string; data_b64: string };

export type ClientMsg =
  | { content: string; force_backend?: string | null; images?: ClientImage[] }
  | { type: "cancel" }
  | { type: "load_session"; session_id: string };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isServerMessage(value: unknown): value is ServerMsg {
  if (!isRecord(value) || typeof value.type !== "string") return false;
  if (value.type === "session") return typeof value.session_id === "string";
  if (value.type === "routing")
    return (
      typeof value.backend === "string" &&
      typeof value.is_local === "boolean" &&
      typeof value.reason === "string" &&
      isRecord(value.classification)
    );
  if (typeof value.content !== "string" || !isRecord(value.meta)) return false;
  if (value.type === "tool_call")
    return (
      typeof value.meta.name === "string" && typeof value.meta.ok === "boolean"
    );
  if (value.type === "recall")
    return (
      Array.isArray(value.meta.hits) &&
      value.meta.hits.every(
        (hit) =>
          isRecord(hit) &&
          typeof hit.id === "number" &&
          typeof hit.text === "string" &&
          typeof hit.role === "string" &&
          typeof hit.session_id === "string" &&
          typeof hit.source === "string" &&
          (hit.distance === null ||
            (typeof hit.distance === "number" &&
              Number.isFinite(hit.distance))),
      )
    );
  return ["status", "text", "image_url", "error", "done"].includes(value.type);
}

export function connectChat(
  onMsg: (m: ServerMsg) => void,
  onClose?: () => void,
  onOpen?: () => void,
): WebSocket {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${window.location.host}/ws/chat`);
  ws.onmessage = (e) => {
    let value: unknown;
    try {
      value = JSON.parse(e.data);
    } catch {
      /* report invalid frames below */
    }
    if (isServerMessage(value)) onMsg(value);
    else
      onMsg({
        type: "error",
        content: "Received an invalid gateway message.",
        meta: { code: "invalid_frame" },
      });
  };
  ws.onclose = () => onClose?.();
  ws.onopen = () => onOpen?.();
  return ws;
}

export function sendChat(ws: WebSocket, msg: ClientMsg): boolean {
  if (ws.readyState !== WebSocket.OPEN) return false;
  try {
    ws.send(JSON.stringify(msg));
    return true;
  } catch {
    return false;
  }
}
