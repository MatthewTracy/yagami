import { useState } from "react";

export type ToolCallInfo = {
  name: string;
  input: Record<string, unknown>;
  ok: boolean;
  result?: string | null;
  error?: string | null;
  artifacts?: Record<string, unknown>;
};

type Props = { call: ToolCallInfo };

export function ToolCallCard({ call }: Props) {
  const [open, setOpen] = useState(false);
  const tone = call.ok
    ? "border-zinc-700 bg-zinc-900/60"
    : "border-red-900/60 bg-red-950/30";
  const preview = call.ok
    ? (call.result ?? "").trim().slice(0, 200)
    : (call.error ?? "error");
  return (
    <div className={`my-2 rounded border ${tone} text-[11px] overflow-hidden`}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full text-left px-2 py-1.5 flex items-center gap-2 hover:bg-zinc-800/40"
      >
        <span>{call.ok ? "🔧" : "⚠"}</span>
        <span className="font-mono text-zinc-300">{call.name}</span>
        <span className="text-zinc-500 truncate flex-1 min-w-0">
          {Object.entries(call.input)
            .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
            .join(", ")}
        </span>
        <span className="text-zinc-500">{open ? "▾" : "▸"}</span>
      </button>
      {!open && preview && (
        <div className="px-2 pb-1.5 text-zinc-400 italic truncate">→ {preview}</div>
      )}
      {open && (
        <div className="px-2 pb-2 space-y-1.5 border-t border-zinc-800/60">
          <div>
            <div className="text-[9px] uppercase tracking-wider text-zinc-500 mt-1">
              Input
            </div>
            <pre className="bg-zinc-950 p-1.5 rounded text-zinc-300 overflow-x-auto">
              {JSON.stringify(call.input, null, 2)}
            </pre>
          </div>
          <div>
            <div className="text-[9px] uppercase tracking-wider text-zinc-500">
              {call.ok ? "Result" : "Error"}
            </div>
            <pre
              className={`p-1.5 rounded overflow-x-auto whitespace-pre-wrap break-words ${
                call.ok ? "bg-zinc-950 text-zinc-200" : "bg-red-950/40 text-red-200"
              }`}
            >
              {call.ok ? call.result || "(empty)" : call.error}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}
