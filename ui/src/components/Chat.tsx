import { useEffect, useRef, useState } from "react";
import { connectChat, ClientImage, sendChat, ServerMsg } from "../lib/ws";
import { AssistantBubble } from "./AssistantBubble";
import { emitToast } from "./Toast";
import { ToolCallInfo } from "./ToolCallCard";

const DRAFT_KEY = "yagami:draft";

type Attachment =
  | { kind: "image"; filename: string; preview_url: string; media_type: string; data_b64: string }
  | { kind: "document"; filename: string; text: string; chars: number; truncated: boolean };

export type RecallHit = {
  id: number;
  role: string;
  text: string;
  session_id: string;
  source: string;
  distance: number | null;
};

type Bubble =
  | { role: "user"; text: string; attachments?: Attachment[] }
  | {
      role: "assistant";
      text: string;
      image?: string;
      pending: boolean;
      pendingHint?: string;
      backend?: string;
      decisionId?: number;
      toolCalls?: ToolCallInfo[];
      recall?: RecallHit[];
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
  const [input, setInput] = useState<string>(() => {
    try {
      return localStorage.getItem(DRAFT_KEY) || "";
    } catch {
      return "";
    }
  });
  const [connected, setConnected] = useState(false);
  const [inFlight, setInFlight] = useState(false);
  const [forceBackend, setForceBackend] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onReset = () => {
      setInput((cur) => (cur.startsWith("/reset ") ? cur : "/reset " + cur));
      const ta = document.querySelector<HTMLTextAreaElement>("textarea");
      ta?.focus();
    };
    window.addEventListener("yagami:reset-phi", onReset);
    return () => window.removeEventListener("yagami:reset-phi", onReset);
  }, []);

  // Persist draft to localStorage on every change, restore on mount above.
  useEffect(() => {
    try {
      if (input) localStorage.setItem(DRAFT_KEY, input);
      else localStorage.removeItem(DRAFT_KEY);
    } catch {
      /* localStorage full / disabled; ignore */
    }
  }, [input]);

  // Global keyboard shortcuts.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      const target = e.target as HTMLElement | null;
      const inField =
        target?.tagName === "TEXTAREA" ||
        target?.tagName === "INPUT" ||
        target?.getAttribute("contenteditable") === "true";

      if (e.key === "Escape" && inFlight && wsRef.current) {
        // Cancel in-flight generation.
        sendChat(wsRef.current, { type: "cancel" });
        return;
      }
      if (mod && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        document.querySelector<HTMLTextAreaElement>("textarea")?.focus();
        return;
      }
      if (mod && (e.key === "l" || e.key === "L") && !inField) {
        // Reload page = fresh session. Avoid in inputs to not trample Ctrl+L
        // address-bar focus expectations when user is typing.
        e.preventDefault();
        window.location.reload();
        return;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [inFlight]);

  async function uploadFiles(files: FileList | File[]) {
    setUploading(true);
    try {
      const list = Array.from(files);
      for (const f of list) {
        const fd = new FormData();
        fd.append("file", f);
        const resp = await fetch("/api/ingest", { method: "POST", body: fd });
        if (!resp.ok) {
          emitToast("error", `Upload failed: ${await resp.text()}`);
          continue;
        }
        const data = await resp.json();
        if (data.kind === "image") {
          setAttachments((a) => [
            ...a,
            {
              kind: "image",
              filename: data.filename,
              media_type: data.media_type,
              data_b64: data.data_b64,
              preview_url: `data:${data.media_type};base64,${data.data_b64}`,
            },
          ]);
        } else {
          setAttachments((a) => [
            ...a,
            {
              kind: "document",
              filename: data.filename,
              text: data.text,
              chars: data.chars,
              truncated: data.truncated,
            },
          ]);
        }
      }
    } finally {
      setUploading(false);
    }
  }

  function onPaste(e: React.ClipboardEvent<HTMLTextAreaElement>) {
    const files: File[] = [];
    for (const it of Array.from(e.clipboardData.items)) {
      if (it.kind === "file") {
        const f = it.getAsFile();
        if (f) files.push(f);
      }
    }
    if (files.length) {
      e.preventDefault();
      uploadFiles(files);
    }
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    if (e.dataTransfer.files?.length) uploadFiles(e.dataTransfer.files);
  }

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
          decisionId: m.decision_id,
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
    if (m.type === "recall") {
      const hits = m.meta.hits as RecallHit[];
      updateLastAssistant((last) => ({ ...last, recall: hits }));
      return;
    }
    if (m.type === "tool_call") {
      const info: ToolCallInfo = {
        name: m.meta.name,
        input: m.meta.input,
        ok: m.meta.ok,
        result: m.meta.result ?? null,
        error: m.meta.error ?? null,
        artifacts: m.meta.artifacts,
      };
      updateLastAssistant((last) => ({
        ...last,
        toolCalls: [...(last.toolCalls ?? []), info],
        pendingHint: `using ${info.name}`,
      }));
      return;
    }
    if (m.type === "error") {
      emitToast("error", m.content);
      updateLastAssistant((last) => ({ ...last, pending: false }));
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
    if ((!text && attachments.length === 0) || !wsRef.current || inFlight) return;

    // Fold document attachments into the message text. Image attachments stay
    // as proper vision content blocks.
    let composed = text;
    const docs = attachments.filter((a) => a.kind === "document");
    for (const d of docs) {
      if (d.kind !== "document") continue;
      composed = `${composed ? composed + "\n\n" : ""}--- attached: ${d.filename} (${d.chars.toLocaleString()} chars${d.truncated ? ", truncated" : ""}) ---\n${d.text}\n--- end ${d.filename} ---`;
    }

    const images: ClientImage[] = attachments
      .filter((a) => a.kind === "image")
      .map((a) => (a.kind === "image" ? { media_type: a.media_type, data_b64: a.data_b64 } : null!))
      .filter(Boolean);

    setBubbles((b) => [...b, { role: "user", text: text || "(attached files)", attachments }]);
    const payload: { content: string; force_backend?: string; images?: ClientImage[] } = {
      content: composed,
    };
    if (forceBackend) payload.force_backend = forceBackend;
    if (images.length) payload.images = images;
    sendChat(wsRef.current, payload);
    setInput("");
    setAttachments([]);
    setInFlight(true);
    const ta = document.querySelector<HTMLTextAreaElement>("textarea");
    if (ta) ta.style.height = "auto";
  }

  function stop() {
    if (wsRef.current && inFlight) {
      sendChat(wsRef.current, { type: "cancel" });
    }
  }

  function regenerate() {
    if (!wsRef.current || inFlight) return;
    // Find the last user message; drop the trailing assistant bubble if any;
    // resend the same content (the server records a new turn - sticky floor
    // and force_backend still apply).
    let userText: string | null = null;
    setBubbles((b) => {
      const copy = [...b];
      while (copy.length && copy[copy.length - 1].role === "assistant") copy.pop();
      const last = copy[copy.length - 1];
      if (last && last.role === "user") userText = last.text;
      return copy;
    });
    setTimeout(() => {
      if (!userText || !wsRef.current) return;
      const payload: { content: string; force_backend?: string } = { content: userText };
      if (forceBackend) payload.force_backend = forceBackend;
      sendChat(wsRef.current, payload);
      setInFlight(true);
    }, 0);
  }

  return (
    <div
      className="flex flex-col flex-1 min-h-0"
      onDragOver={(e) => e.preventDefault()}
      onDrop={onDrop}
    >
      <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto p-4 space-y-3">
        {bubbles.length === 0 && (
          <div className="text-zinc-500 text-sm">
            Say hi. Try "what is 2+2", "draw a red sailboat", or paste a long passage.
          </div>
        )}
        {bubbles.map((b, i) => {
          if (b.role === "user") {
            return (
              <div
                key={i}
                className="max-w-2xl px-3 py-2 rounded-lg text-sm whitespace-pre-wrap bg-zinc-800 ml-auto"
              >
                {b.text}
              </div>
            );
          }
          const lastAssistantIdx = (() => {
            for (let j = bubbles.length - 1; j >= 0; j--) {
              if (bubbles[j].role === "assistant") return j;
            }
            return -1;
          })();
          return (
            <AssistantBubble
              key={i}
              text={b.text}
              image={b.image}
              pending={b.pending}
              pendingHint={b.pendingHint}
              isLastAssistant={i === lastAssistantIdx && !inFlight}
              onRegenerate={regenerate}
              decisionId={b.decisionId}
              toolCalls={b.toolCalls}
              recall={b.recall}
            />
          );
        })}
      </div>
      <div className="p-3 border-t border-zinc-800 flex gap-2 items-end shrink-0">
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".txt,.md,.markdown,.pdf,.log,.csv,.json,image/*"
          onChange={(e) => {
            if (e.target.files?.length) uploadFiles(e.target.files);
            e.target.value = "";
          }}
          className="hidden"
        />
        <button
          onClick={() => fileInputRef.current?.click()}
          disabled={!connected || inFlight || uploading}
          title="Attach file (PDF, MD, TXT, image) - or drag-drop / paste"
          className="px-2 py-2 text-zinc-400 hover:text-zinc-100 text-base disabled:opacity-50"
        >
          {uploading ? "…" : "📎"}
        </button>
        <div className="flex-1 min-w-0">
          {attachments.length > 0 && (
            <div className="flex flex-wrap gap-1 mb-1">
              {attachments.map((a, i) => (
                <span
                  key={i}
                  className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-zinc-800 text-[11px] text-zinc-300"
                >
                  {a.kind === "image" ? (
                    <img src={a.preview_url} className="h-4 w-4 object-cover rounded-sm" alt="" />
                  ) : (
                    <span>📄</span>
                  )}
                  <span className="max-w-[180px] truncate">{a.filename}</span>
                  <button
                    onClick={() => setAttachments((arr) => arr.filter((_, j) => j !== i))}
                    className="text-zinc-500 hover:text-red-400"
                    title="Remove"
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}
          <textarea
            rows={1}
            className="block w-full bg-zinc-900 border border-zinc-800 rounded-md px-3 py-2 text-sm outline-none focus:border-zinc-600 disabled:opacity-50 resize-none max-h-64 overflow-y-auto"
            placeholder={
              !connected
                ? "connecting…"
                : inFlight
                  ? "waiting for reply…"
                  : "Message Yagami… (Shift+Enter newline · /cloud /local /image /think /code · 📎/drop/paste files)"
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
            onPaste={onPaste}
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

