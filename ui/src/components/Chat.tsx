import { useEffect, useRef, useState } from "react";
import { connectChat, ServerMsg } from "../lib/ws";

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

type Props = { onRouting: (r: Routing) => void };

const PENDING_HINT: Record<string, string> = {
  ollama: "thinking locally",
  anthropic: "asking Claude",
  stability: "generating image (this can take 5–15s)",
  echo: "echoing",
};

export function Chat({ onRouting }: Props) {
  const [bubbles, setBubbles] = useState<Bubble[]>([]);
  const [input, setInput] = useState("");
  const [connected, setConnected] = useState(false);
  const [inFlight, setInFlight] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const ws = connectChat(handle, () => setConnected(false));
    ws.onopen = () => setConnected(true);
    wsRef.current = ws;
    return () => ws.close();
  }, []);

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
    }
  }

  function send() {
    const text = input.trim();
    if (!text || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN || inFlight) return;
    setBubbles((b) => [...b, { role: "user", text }]);
    wsRef.current.send(JSON.stringify({ content: text }));
    setInput("");
    setInFlight(true);
  }

  const busy = !connected || inFlight;

  return (
    <div className="flex flex-col h-full">
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-3">
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
      <div className="p-3 border-t border-zinc-800 flex gap-2">
        <input
          className="flex-1 bg-zinc-900 border border-zinc-800 rounded-md px-3 py-2 text-sm outline-none focus:border-zinc-600 disabled:opacity-50"
          placeholder={
            !connected ? "connecting…" : inFlight ? "waiting for reply…" : "Message Yagami…"
          }
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          disabled={busy}
        />
        <button
          onClick={send}
          disabled={busy}
          className="px-4 py-2 bg-emerald-700 hover:bg-emerald-600 disabled:bg-zinc-800 disabled:text-zinc-500 rounded-md text-sm"
        >
          {inFlight ? "…" : "Send"}
        </button>
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
