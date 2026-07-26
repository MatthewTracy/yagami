import { useState } from "react";

export type ToolCallInfo = {
  name: string;
  ok: boolean;
  errorCode?: string | null;
  resultBytes?: number;
  artifacts?: Record<string, unknown>;
};

type Props = { call: ToolCallInfo };

export function ToolCallCard({ call }: Props) {
  const [open, setOpen] = useState(false);
  const tone = call.ok
    ? "border-zinc-700 bg-zinc-900/60"
    : "border-red-900/60 bg-red-950/30";
  const status = call.ok
    ? `completed (${call.resultBytes ?? 0} response bytes)`
    : call.errorCode ?? "tool_failed";
  return (
    <div className={`my-2 overflow-hidden rounded border ${tone} text-[11px]`}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-2 px-2 py-1.5 text-left hover:bg-zinc-800/40"
      >
        <span aria-hidden="true">{call.ok ? "✓" : "!"}</span>
        <span className="font-mono text-zinc-300">{call.name}</span>
        <span className="min-w-0 flex-1 truncate text-zinc-500">{status}</span>
        <span className="text-zinc-500" aria-hidden="true">
          {open ? "▾" : "▸"}
        </span>
      </button>
      {open && (
        <div className="border-t border-zinc-800/60 px-2 pb-2">
          <div className="mt-1 text-[9px] uppercase tracking-wider text-zinc-500">
            Content-free evidence
          </div>
          <pre className="overflow-x-auto rounded bg-zinc-950 p-1.5 text-zinc-300">
            {JSON.stringify(
              {
                status,
                ...call.artifacts,
              },
              null,
              2,
            )}
          </pre>
        </div>
      )}
    </div>
  );
}
