import { useEffect, useRef, useState } from "react";
import { connectChat, sendChat, ServerMsg } from "../lib/ws";

type Bubble =
  | { role: "user"; text: string }
  | {
      role: "assistant";
      text: string;
      image?: string;
      pending: boolean;
      pendingHint?: string;
      backend?: string;
    };

type Routing = {
  backend: string;
  isLocal: boolean;
  reason: string;
  classification: Record<string, unknown>;
};

type Props = {
  onRouting: (r: Routing) => void;
  onSession: (sessionId: string) => void;
  onTurnComplete: () => void;
  loadSessionId: string | null;
};

const PENDING_HINT: Record<string, string> = {
  ollama: "thinking locally",
  anthropic: "asking Claude",
  stability: "generating image (this can take 5–15s)",
  echo: "echoing",
};

const FORCE_OPTIONS = [
  { value: "", label: "Auto" },
  { value: "ollama", label: "Local (Ollama)" },
  { value: "anthropic", label: "Cloud (Claude)" },
  { value: "stability", label: "Image (Stability)" },
];

export function Chat({ onRouting, onSession, onTurnComplete, loadSessionId }: Props) {
  const [bubbles, setBubbles] = useState<Bubble[]>([]);
  const [input, setInput] = useState("");
  const [connected, setConnected] = useState(false);
  const [inFlight, setInFlight] = useState(false);
  const [forceBackend, setForceBackend] = useState("");
  const wsRef = useRef<WebSocket | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const ws = connectChat(handle, () => setConnected(false), () => setConnected(true));
    wsRef.current = ws;
    return () => ws.close();
  }, []);

  useEffect(() => {
    if (loadSessionId && wsRef.current?.readyState === WebSocket.OPEN) {
      setBubbles([]);
      sendChat(wsRef.current, { type: "load_session", session_id: loadSessionId });
      fetch(`/api/sessions/${loadSessionId}`)
        .then((r) => r.json())
        .then((d) => {
          const loaded: Bubble[] = (d.messages || []).map((m: { role: string; content: string }) =>
            m.role === "user"
              ? { role: "user", text: m.content }
              : { role: "assistant", text: m.content, pending: false }
          );
          setBubbles(loaded);
        });
    }
  }, [loadSessionId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [bubbles]);

  function updateLastAssistant(patch: (last: Extract<Bubble, { role: "assistant" }>) => Bubble) {
    setBubbles((b) => {
      const last = b[b.length - 1];
      if (last?.role !== "assistant") return b;
      return [...b.slice(0, -1), patch(last)];
    });
  }

  function handle(m: ServerMsg) {
    if (m.type === "session") {
      onSession(m.session_id);
      return;
    }
    if (m.type === "routing") {
      onRouting({
        backend: m.backend,
        isLocal: m.is_local,
        reason: m.reason,
        classification: m.classification,
      });
      setBubbles((b) => [
        ...b,
        {
          role: "assistant",
          text: "",
          pending: true,
          pendingHint: PENDING_HINT[m.backend] ?? `calling ${m.backend}`,
          backend: m.backend,
        },
      ]);
      return;
    }
    if (m.type === "text") {
      updateLastAssistant((last) => ({ ...last, text: last.text + m.content, pending: false }));
      return;
    }
    if (m.type === "image_url") {
      updateLastAssistant((last) => ({ ...last, image: m.content, pending: false }));
      return;
    }
    if (m.type === "error") {
      updateLastAssistant((last) => ({
        ...last,
        text: (last.text ? last.text + "\n" : "") + `error: ${m.content}`,
        pending: false,
      }));
      return;
    }
    if (m.type === "done") {
      updateLastAssistant((last) => ({ ...last, pending: false }));
      setInFlight(false);
      onTurnComplete();
    }
  }

  function send() {
    const text = input.trim();
    if (!text || !wsRef.current || inFlight) return;
    setBubbles((b) => [...b, { role: "user", text }]);
    const payload: { content: string; force_backend?: string } = { content: text };
    if (forceBackend) payload.force_backend = forceBackend;
    sendChat(wsRef.current, payload);
    setInput("");
    setInFlight(true);
    const ta = document.querySelector<HTMLTextAreaElement>("textarea");
    if (ta) ta.style.height = "auto";
  }

  function stop() {
    if (wsRef.current && inFlight) {
      sendChat(wsRef.current, { type: "cancel" });
    }
  }

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto p-4 space-y-3">
        {bubbles.length === 0 && (
          <div className="text-zinc-500 text-sm">
            Say hi. Try "what is 2+2", "draw a red sailboat", or paste a long passage.
          </div>
        )}
        {bubbles.map((b, i) => {
          const isAssistant = b.role === "assistant";
          const pending = isAssistant && b.pending;
          return (
            <div
              key={i}
              className={`max-w-2xl px-3 py-2 rounded-lg text-sm whitespace-pre-wrap ${
                b.role === "user"
                  ? "bg-zinc-800 ml-auto"
                  : "bg-zinc-900 border border-zinc-800"
              }`}
            >
              {pending && !b.text && (
                <div className="flex items-center gap-2 text-zinc-400">
                  <ThinkingDots />
                  <span className="text-xs italic">{b.pendingHint}</span>
                </div>
              )}
              {b.text}
              {pending && b.text && <span className="inline-block w-2 h-4 ml-0.5 align-text-bottom bg-zinc-500 animate-pulse" />}
              {isAssistant && b.image && (
                <img src={b.image} alt="generated" className="mt-2 rounded max-w-full" />
              )}
            </div>
          );
        })}
      </div>
      <div className="p-3 border-t border-zinc-800 flex gap-2 items-end shrink-0">
        <div className="flex-1 min-w-0">
          <textarea
            rows={1}
            className="block w-full bg-zinc-900 border border-zinc-800 rounded-md px-3 py-2 text-sm outline-none focus:border-zinc-600 disabled:opacity-50 resize-none max-h-64 overflow-y-auto"
            placeholder={
              !connected
                ? "connecting…"
                : inFlight
                  ? "waiting for reply…"
                  : "Message Yagami… (Shift+Enter newline · /cloud /local /image /think /code to force route)"
            }
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
              const el = e.currentTarget;
              el.style.height = "auto";
              el.style.height = Math.min(el.scrollHeight, 256) + "px";
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            disabled={!connected || inFlight}
          />
          {input.length > 200 && (
            <div className="text-[10px] text-zinc-500 mt-1 px-1">
              {input.length.toLocaleString()} chars
              {input.length > 6000 && " · will route to cloud unless flagged sensitive"}
            </div>
          )}
        </div>
        <select
          value={forceBackend}
          onChange={(e) => setForceBackend(e.target.value)}
          disabled={!connected || inFlight}
          title="Force routing to a specific backend (PHI guard still applies)"
          className="bg-zinc-900 border border-zinc-800 rounded-md px-2 py-2 text-xs text-zinc-300 disabled:opacity-50 focus:border-zinc-600 outline-none"
        >
          {FORCE_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        {inFlight ? (
          <button
            onClick={stop}
            className="px-4 py-2 bg-red-700 hover:bg-red-600 rounded-md text-sm"
          >
            Stop
          </button>
        ) : (
          <button
            onClick={send}
            disabled={!connected}
            className="px-4 py-2 bg-emerald-700 hover:bg-emerald-600 disabled:bg-zinc-800 disabled:text-zinc-500 rounded-md text-sm"
          >
            Send
          </button>
        )}
      </div>
    </div>
  );
}

function ThinkingDots() {
  return (
    <span className="inline-flex gap-1">
      <span className="w-1.5 h-1.5 rounded-full bg-zinc-500 animate-bounce [animation-delay:-0.3s]" />
      <span className="w-1.5 h-1.5 rounded-full bg-zinc-500 animate-bounce [animation-delay:-0.15s]" />
      <span className="w-1.5 h-1.5 rounded-full bg-zinc-500 animate-bounce" />
    </span>
  );
}
