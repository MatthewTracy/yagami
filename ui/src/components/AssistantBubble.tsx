import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import "highlight.js/styles/github-dark.css";
import { ToolCallCard, ToolCallInfo } from "./ToolCallCard";

export type RecallHit = {
  id: number;
  role: string;
  text: string;
  session_id: string;
  source: string;
  distance: number | null;
};

type Props = {
  text: string;
  image?: string;
  pending: boolean;
  pendingHint?: string;
  isLastAssistant: boolean;
  onRegenerate?: () => void;
  decisionId?: number;
  toolCalls?: ToolCallInfo[];
  recall?: RecallHit[];
};

export function AssistantBubble({
  text,
  image,
  pending,
  pendingHint,
  isLastAssistant,
  onRegenerate,
  decisionId,
  toolCalls,
  recall,
}: Props) {
  const [copied, setCopied] = useState(false);
  const [rating, setRating] = useState<-1 | 0 | 1>(0);
  const [recallOpen, setRecallOpen] = useState(false);

  async function sendFeedback(r: -1 | 1) {
    if (!decisionId) return;
    const next = rating === r ? 0 : r; // toggle off if clicked again
    setRating(next);
    if (next === 0) return; // nothing to post; we don't have a delete endpoint
    try {
      await fetch(`/api/decisions/${decisionId}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rating: next }),
      });
    } catch {
      /* fire and forget; user can re-click if they care */
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // ignore
    }
  }

  return (
    <div className="group max-w-2xl px-3 py-2 rounded-lg text-sm bg-zinc-900 border border-zinc-800 relative">
      {recall && recall.length > 0 && (
        <div className="mb-1">
          <button
            onClick={() => setRecallOpen((v) => !v)}
            className="text-[10px] text-zinc-400 hover:text-zinc-200 flex items-center gap-1"
          >
            <span>🧠</span>
            <span>
              recalled {recall.length} from prior session{recall.length === 1 ? "" : "s"}
            </span>
            <span className="text-zinc-600">{recallOpen ? "▾" : "▸"}</span>
          </button>
          {recallOpen && (
            <div className="mt-1 space-y-1">
              {recall.map((h) => (
                <div
                  key={h.id}
                  className="text-[10px] p-1.5 rounded border border-zinc-800 bg-zinc-950/40"
                >
                  <div className="text-zinc-500 mb-0.5">
                    {h.role} · {h.session_id.slice(0, 8)} · {h.source}
                    {h.distance != null ? ` · d=${h.distance.toFixed(3)}` : ""}
                  </div>
                  <div className="text-zinc-300 break-words">{h.text}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      {toolCalls && toolCalls.length > 0 && (
        <div className="mb-1">
          {toolCalls.map((c, i) => (
            <ToolCallCard key={i} call={c} />
          ))}
        </div>
      )}
      {pending && !text && (
        <div className="flex items-center gap-2 text-zinc-400">
          <ThinkingDots />
          <span className="text-xs italic">{pendingHint}</span>
        </div>
      )}
      {text && (
        <div className="prose prose-invert prose-sm max-w-none break-words [&_pre]:bg-zinc-950 [&_pre]:border [&_pre]:border-zinc-800 [&_pre]:rounded [&_pre]:p-2 [&_code]:text-[12px] [&_p]:my-1 [&_ul]:my-1 [&_ol]:my-1 [&_h1]:text-base [&_h1]:font-semibold [&_h2]:text-sm [&_h2]:font-semibold [&_h3]:text-sm [&_h3]:font-semibold">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[rehypeHighlight]}
          >
            {text}
          </ReactMarkdown>
        </div>
      )}
      {pending && text && (
        <span className="inline-block w-2 h-4 ml-0.5 align-text-bottom bg-zinc-500 animate-pulse" />
      )}
      {image && (
        <img src={image} alt="generated" className="mt-2 rounded max-w-full" />
      )}
      {!pending && (text || image) && (
        <div className="absolute top-1.5 right-1.5 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
          {decisionId && (
            <>
              <button
                onClick={() => sendFeedback(1)}
                title="Helpful"
                className={`text-[10px] px-1.5 py-0.5 rounded ${
                  rating === 1
                    ? "bg-emerald-800 text-emerald-100"
                    : "bg-zinc-800/80 hover:bg-zinc-700 text-zinc-300"
                }`}
              >
                ▲
              </button>
              <button
                onClick={() => sendFeedback(-1)}
                title="Not helpful"
                className={`text-[10px] px-1.5 py-0.5 rounded ${
                  rating === -1
                    ? "bg-red-900 text-red-100"
                    : "bg-zinc-800/80 hover:bg-zinc-700 text-zinc-300"
                }`}
              >
                ▼
              </button>
            </>
          )}
          {text && (
            <button
              onClick={copy}
              title={copied ? "Copied" : "Copy"}
              className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-800/80 hover:bg-zinc-700 text-zinc-300"
            >
              {copied ? "✓" : "copy"}
            </button>
          )}
          {isLastAssistant && onRegenerate && (
            <button
              onClick={onRegenerate}
              title="Regenerate"
              className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-800/80 hover:bg-zinc-700 text-zinc-300"
            >
              ↻
            </button>
          )}
        </div>
      )}
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
