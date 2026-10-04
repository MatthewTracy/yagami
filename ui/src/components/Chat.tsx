import { useEffect, useRef, useState } from "react";
import { connectChat, ClientImage, sendChat, ServerMsg } from "../lib/ws";
import { AssistantBubble } from "./AssistantBubble";
import { emitToast } from "./Toast";
import { ToolCallInfo } from "./ToolCallCard";
import { Icon } from "./Icon";

const DRAFT_KEY = "yagami:draft";

type Attachment =
  | {
      kind: "image";
      filename: string;
      preview_url: string;
      media_type: string;
      data_b64: string;
    }
  | {
      kind: "document";
      filename: string;
      text: string;
      chars: number;
      truncated: boolean;
    };

export type RecallHit = {
  id: number;
  role: string;
  text: string;
  session_id: string;
  source: string;
  distance: number | null;
};

type Bubble =
  | {
      role: "user";
      text: string;
      payloadText?: string;
      attachments?: Attachment[];
    }
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
  ollama: "Preparing local generation",
  anthropic: "Contacting Claude",
  stability: "Generating image (this can take 5–15s)",
  echo: "echoing",
};

const FORCE_OPTIONS = [
  { value: "", label: "Auto" },
  { value: "ollama", label: "Local (Ollama)" },
  { value: "anthropic", label: "Cloud (Claude)" },
  { value: "stability", label: "Image (Stability)" },
];

export function Chat({
  onRouting,
  onSession,
  onTurnComplete,
  loadSessionId,
}: Props) {
  const [bubbles, setBubbles] = useState<Bubble[]>([]);
  const [input, setInput] = useState<string>(() => {
    try {
      return sessionStorage.getItem(DRAFT_KEY) || "";
    } catch {
      return "";
    }
  });
  const [connection, setConnection] = useState<
    "connecting" | "connected" | "disconnected"
  >("connecting");
  const [connectionKey, setConnectionKey] = useState(0);
  const connected = connection === "connected";
  const [inFlight, setInFlight] = useState(false);
  const [forceBackend, setForceBackend] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const handleRef = useRef(handle);
  const activeSessionRef = useRef<string | null>(null);
  handleRef.current = handle;

  useEffect(() => {
    const onReset = () => {
      setInput((cur) => (cur.startsWith("/reset ") ? cur : "/reset " + cur));
      const ta = document.querySelector<HTMLTextAreaElement>("textarea");
      ta?.focus();
    };
    window.addEventListener("yagami:reset-phi", onReset);
    return () => window.removeEventListener("yagami:reset-phi", onReset);
  }, []);

  // Retain drafts only for this browser tab. Unsent private text must not
  // survive a browser restart or fall outside Yagami's server retention controls.
  useEffect(() => {
    try {
      if (input) sessionStorage.setItem(DRAFT_KEY, input);
      else sessionStorage.removeItem(DRAFT_KEY);
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
        window.dispatchEvent(new Event("yagami:new-chat"));
        return;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [inFlight]);

  async function uploadFiles(files: FileList | File[]) {
    if (!connected || inFlight || uploading) return;
    setUploading(true);
    try {
      const list = Array.from(files);
      for (const f of list) {
        try {
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
        } catch {
          emitToast(
            "error",
            `Upload failed: could not reach the server (${f.name})`,
          );
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
    let active = true;
    setConnection("connecting");
    const ws = connectChat(
      (message) => {
        if (active) handleRef.current(message);
      },
      () => {
        if (!active) return;
        setConnection("disconnected");
        setInFlight(false);
        updateLastAssistant((last) => ({ ...last, pending: false }));
      },
      () => {
        if (!active) return;
        setConnection("connected");
        if (!loadSessionId && activeSessionRef.current)
          sendChat(ws, {
            type: "load_session",
            session_id: activeSessionRef.current,
          });
      },
    );
    wsRef.current = ws;
    return () => {
      active = false;
      ws.close();
    };
  }, [connectionKey]);

  useEffect(() => {
    if (loadSessionId && wsRef.current?.readyState === WebSocket.OPEN) {
      let cancelled = false;
      if (
        !sendChat(wsRef.current, {
          type: "load_session",
          session_id: loadSessionId,
        })
      )
        return;
      setBubbles([]);
      setInFlight(false);
      fetch(`/api/sessions/${encodeURIComponent(loadSessionId)}`)
        .then((r) => {
          if (!r.ok) throw new Error(`session load failed (${r.status})`);
          return r.json();
        })
        .then((d) => {
          if (cancelled) return;
          const loaded: Bubble[] = (d.messages || []).map(
            (m: {
              role: string;
              content: string;
              images?: { media_type: string; data_b64: string }[];
            }) => {
              if (m.role !== "user") {
                return { role: "assistant", text: m.content, pending: false };
              }
              const savedImages: Attachment[] = (m.images || []).map(
                (image, index) => ({
                  kind: "image",
                  filename: `Saved image ${index + 1}`,
                  media_type: image.media_type,
                  data_b64: image.data_b64,
                  preview_url: `data:${image.media_type};base64,${image.data_b64}`,
                }),
              );
              return {
                role: "user",
                text: m.content || "(attached images)",
                payloadText: m.content,
                attachments: savedImages,
              };
            },
          );
          setBubbles(loaded);
        })
        .catch(
          () => !cancelled && emitToast("error", "Failed to load conversation"),
        );
      return () => {
        cancelled = true;
      };
    }
  }, [loadSessionId, connected]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [bubbles]);

  function updateLastAssistant(
    patch: (last: Extract<Bubble, { role: "assistant" }>) => Bubble,
  ) {
    setBubbles((b) => {
      const last = b[b.length - 1];
      if (last?.role !== "assistant") return b;
      return [...b.slice(0, -1), patch(last)];
    });
  }

  function handle(m: ServerMsg) {
    if (m.type === "session") {
      activeSessionRef.current = m.session_id;
      onSession(m.session_id);
      return;
    }
    if (m.type === "status") {
      setBubbles((b) => {
        const last = b[b.length - 1];
        if (last?.role === "assistant" && last.pending && !last.text) {
          return [...b.slice(0, -1), { ...last, pendingHint: m.content }];
        }
        return [
          ...b,
          {
            role: "assistant",
            text: "",
            pending: true,
            pendingHint: m.content,
            backend: m.meta.backend,
          },
        ];
      });
      return;
    }
    if (m.type === "routing") {
      onRouting({
        backend: m.backend,
        isLocal: m.is_local,
        reason: m.reason,
        classification: m.classification,
      });
      setBubbles((b) => {
        const last = b[b.length - 1];
        const routed = {
          role: "assistant",
          text: "",
          pending: true,
          pendingHint: PENDING_HINT[m.backend] ?? `Calling ${m.backend}`,
          backend: m.backend,
          decisionId: m.decision_id,
        } as const;
        if (last?.role === "assistant" && last.pending && !last.text) {
          return [...b.slice(0, -1), { ...last, ...routed }];
        }
        return [...b, routed];
      });
      return;
    }
    if (m.type === "text") {
      updateLastAssistant((last) => ({
        ...last,
        text: last.text + m.content,
        pending: false,
      }));
      return;
    }
    if (m.type === "image_url") {
      updateLastAssistant((last) => ({
        ...last,
        image: m.content,
        pending: false,
      }));
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
        ok: m.meta.ok,
        errorCode: m.meta.error_code ?? null,
        resultBytes: m.meta.result_bytes ?? 0,
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
      setInFlight(false);
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
    if (
      (!text && attachments.length === 0) ||
      !wsRef.current ||
      !connected ||
      inFlight ||
      uploading
    )
      return;

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
      .map((a) =>
        a.kind === "image"
          ? { media_type: a.media_type, data_b64: a.data_b64 }
          : null!,
      )
      .filter(Boolean);

    const payload: {
      content: string;
      force_backend?: string;
      images?: ClientImage[];
    } = {
      content: composed,
    };
    if (forceBackend) payload.force_backend = forceBackend;
    if (images.length) payload.images = images;
    if (!sendChat(wsRef.current, payload)) {
      emitToast("error", "Message was not sent. Your draft has been kept.");
      return;
    }
    setBubbles((b) => [
      ...b,
      {
        role: "user",
        text: text || "(attached files)",
        payloadText: composed,
        attachments,
      },
    ]);
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
    if (!wsRef.current || !connected || inFlight || uploading) return;
    // Find the last user message; drop the trailing assistant bubble if any;
    // resend the same content (the server records a new turn - sticky floor
    // and force_backend still apply).
    let userIndex = bubbles.length - 1;
    while (userIndex >= 0 && bubbles[userIndex].role !== "user") userIndex -= 1;
    const userBubble = bubbles[userIndex];
    if (!userBubble || userBubble.role !== "user") return;
    const images: ClientImage[] = (userBubble.attachments || [])
      .filter((a) => a.kind === "image")
      .map((a) => ({ media_type: a.media_type, data_b64: a.data_b64 }));
    const payload: {
      content: string;
      force_backend?: string;
      images?: ClientImage[];
    } = {
      content: userBubble.payloadText ?? userBubble.text,
    };
    if (forceBackend) payload.force_backend = forceBackend;
    if (images.length) payload.images = images;
    if (!sendChat(wsRef.current, payload)) {
      emitToast("error", "Could not regenerate. Reconnect and try again.");
      return;
    }
    setBubbles(bubbles.slice(0, userIndex + 1));
    setInFlight(true);
  }

  return (
    <div
      className="flex flex-col flex-1 min-h-0"
      onDragOver={(e) => e.preventDefault()}
      onDrop={onDrop}
    >
      <div
        ref={scrollRef}
        className={`chat-scroll flex-1 min-h-0 overflow-y-auto space-y-4 ${bubbles.length === 0 ? "flex" : ""}`}
      >
        {bubbles.length === 0 && (
          <div className="welcome">
            <div className="welcome-icon">
              <Icon name="shield" size={28} />
            </div>
            <p className="welcome-eyebrow">A workspace for governed AI</p>
            <h2>
              Your context.
              <br />
              Your control.
            </h2>
            <p className="welcome-copy">
              Work with local and cloud models through one policy gateway. Each
              turn has a route, a reason, and a record you can review.
            </p>
            <div className="suggestion-grid">
              <button
                className="suggestion-card"
                disabled={!connected}
                onClick={() => {
                  setInput(
                    "Explain how an AI context firewall works in plain language.",
                  );
                  document.getElementById("message-input")?.focus();
                }}
              >
                <Icon name="message" />
                <strong>Explore a topic</strong>
                <span>Get a clear explanation.</span>
              </button>
              <button
                className="suggestion-card"
                disabled={!connected}
                onClick={() => {
                  setInput(
                    "Help me break a complex project into practical next steps.",
                  );
                  document.getElementById("message-input")?.focus();
                }}
              >
                <Icon name="activity" />
                <strong>Plan your next step</strong>
                <span>Turn a problem into a plan.</span>
              </button>
              <button
                className="suggestion-card"
                disabled={!connected}
                onClick={() => fileInputRef.current?.click()}
              >
                <Icon name="document" />
                <strong>Review a document</strong>
                <span>Attach a file to get started.</span>
              </button>
            </div>
          </div>
        )}
        {bubbles.map((b, i) => {
          if (b.role === "user") {
            return (
              <div
                key={i}
                className="max-w-2xl px-4 py-3 rounded-xl text-sm whitespace-pre-wrap break-words bg-zinc-800 ml-auto"
              >
                {b.text}
                {b.attachments?.some((a) => a.kind === "image") && (
                  <div className="flex flex-wrap gap-2 mt-2">
                    {b.attachments.map((a, attachmentIndex) =>
                      a.kind === "image" ? (
                        <img
                          key={attachmentIndex}
                          src={a.preview_url}
                          alt={a.filename}
                          className="max-h-40 max-w-48 rounded border border-zinc-700 object-contain"
                        />
                      ) : null,
                    )}
                  </div>
                )}
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
      <div className="composer-area shrink-0">
        <div className="composer-status" role="status">
          <span className={`status-dot ${connection}`} />
          <span>
            {connected
              ? "Connected to gateway"
              : connection === "connecting"
                ? "Connecting to gateway…"
                : "Disconnected from gateway"}
          </span>
          {connection === "disconnected" && (
            <button
              className="underline underline-offset-4 ml-auto"
              onClick={() => setConnectionKey((key) => key + 1)}
            >
              Reconnect
            </button>
          )}
        </div>
        <div className="composer-row">
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
            aria-label="Attach file"
            className="toolbar-button min-h-11 disabled:opacity-50"
          >
            {uploading ? "…" : <Icon name="paperclip" size={20} />}
          </button>
          <div className="composer-input">
            {attachments.length > 0 && (
              <div className="flex flex-wrap gap-1 mb-1">
                {attachments.map((a, i) => (
                  <span
                    key={i}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-zinc-800 text-[11px] text-zinc-300"
                  >
                    {a.kind === "image" ? (
                      <img
                        src={a.preview_url}
                        className="h-4 w-4 object-cover rounded-sm"
                        alt=""
                      />
                    ) : (
                      <span>📄</span>
                    )}
                    <span className="max-w-[180px] truncate">{a.filename}</span>
                    <button
                      onClick={() =>
                        setAttachments((arr) => arr.filter((_, j) => j !== i))
                      }
                      className="text-zinc-400 hover:text-red-400"
                      title="Remove"
                      aria-label={`Remove ${a.filename}`}
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
            )}
            <textarea
              id="message-input"
              aria-label="Message Yagami"
              rows={1}
              className="composer-textarea"
              placeholder={
                !connected
                  ? "connecting…"
                  : inFlight
                    ? "waiting for reply…"
                    : "Message Yagami…"
              }
              value={input}
              onChange={(e) => {
                setInput(e.target.value);
                const el = e.currentTarget;
                el.style.height = "auto";
                el.style.height = Math.min(el.scrollHeight, 256) + "px";
              }}
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !e.nativeEvent.isComposing
                ) {
                  e.preventDefault();
                  send();
                }
              }}
              onPaste={onPaste}
              disabled={!connected || inFlight}
            />
            {input.length > 200 && (
              <div className="text-[10px] text-zinc-400 mt-1 px-1">
                {input.length.toLocaleString()} chars
                {input.length > 6000 && " · routing remains subject to policy"}
              </div>
            )}
          </div>
          <select
            value={forceBackend}
            onChange={(e) => setForceBackend(e.target.value)}
            disabled={!connected || inFlight}
            title="Force routing to a specific backend (PHI guard still applies)"
            aria-label="Routing preference"
            className="composer-select disabled:opacity-50"
          >
            {FORCE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          {inFlight ? (
            <button onClick={stop} className="send-button stop-button">
              Stop
            </button>
          ) : (
            <button
              onClick={send}
              disabled={
                !connected ||
                uploading ||
                (!input.trim() && attachments.length === 0)
              }
              className="send-button"
            >
              Send <Icon name="arrow" size={16} />
            </button>
          )}
        </div>
        <div className="composer-hint">
          <span>Shift + Enter for a new line · Attach or drop files</span>
          <button
            onClick={() => window.dispatchEvent(new Event("yagami:shortcuts"))}
            className="shrink-0 hover:text-white"
            aria-label="Keyboard shortcuts"
          >
            Shortcuts
          </button>
        </div>
      </div>
    </div>
  );
}
