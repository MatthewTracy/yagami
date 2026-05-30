import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import "highlight.js/styles/github-dark.css";

type Props = {
  text: string;
  image?: string;
  pending: boolean;
  pendingHint?: string;
  isLastAssistant: boolean;
  onRegenerate?: () => void;
};

export function AssistantBubble({
  text,
  image,
  pending,
  pendingHint,
  isLastAssistant,
  onRegenerate,
}: Props) {
  const [copied, setCopied] = useState(false);

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
