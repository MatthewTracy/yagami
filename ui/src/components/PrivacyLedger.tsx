import { useEffect, useState } from "react";

type DecisionRow = {
  id: number;
  session_id: string;
  created_at: number;
  backend: string;
  is_local: boolean;
  reason: string;
  classification: Record<string, unknown>;
  scrubbed_preview: string;
  source: string;
  t_classify_ms: number | null;
  t_first_token_ms: number | null;
  t_total_ms: number | null;
  profile: string | null;
};

function fmtMs(ms: number | null): string {
  if (ms == null) return "-";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

type Props = { sessionId: string | null; refreshKey: number };

export function PrivacyLedger({ sessionId, refreshKey }: Props) {
  const [rows, setRows] = useState<DecisionRow[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    setLoading(true);
    fetch(`/api/decisions?session_id=${sessionId}&limit=50`)
      .then((r) => r.json())
      .then((d) => {
        if (!cancelled) setRows(d.decisions || []);
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [sessionId, refreshKey]);

  if (!sessionId) {
    return <div className="text-xs text-zinc-500">No session yet.</div>;
  }
  if (loading && rows.length === 0) {
    return <div className="text-xs text-zinc-500">Loading…</div>;
  }
  if (rows.length === 0) {
    return <div className="text-xs text-zinc-500">No routing decisions yet.</div>;
  }

  const sessionHasPhi = rows.some((r) => {
    const s = r.classification?.sensitivity as string | undefined;
    return s && s !== "none";
  });

  return (
    <div className="space-y-2">
      <div className="flex justify-end">
        <a
          href={`/api/decisions/export?session_id=${sessionId}`}
          download
          className="text-[10px] text-zinc-500 hover:text-zinc-300 underline underline-offset-2"
          title="Download this session's routing decisions as CSV"
        >
          Export CSV
        </a>
      </div>
      {sessionHasPhi && (
        <div className="text-[11px] p-2 rounded border border-amber-900/50 bg-amber-900/10 text-amber-200 flex items-start gap-2">
          <span className="text-base leading-none">🔒</span>
          <div className="flex-1 min-w-0">
            <div className="font-medium">Session contains sensitive content</div>
            <div className="text-amber-200/70 mt-0.5">
              Cloud text routes are blocked. Image gen still works (only the
              prompt is sent). Use Reset to bypass for one turn.
            </div>
          </div>
          <button
            onClick={() => window.dispatchEvent(new CustomEvent("yagami:reset-phi"))}
            className="shrink-0 text-amber-100 hover:text-white underline underline-offset-2"
            title="Prefill input with /reset"
          >
            Reset
          </button>
        </div>
      )}
      {rows.map((r) => (
        <div
          key={r.id}
          className={`text-xs p-2 rounded border ${
            r.is_local
              ? "border-zinc-800 bg-zinc-900/40"
              : "border-amber-900/50 bg-amber-900/10"
          }`}
        >
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <span className="font-mono text-zinc-200">{r.backend}</span>
              {r.is_local ? (
                <span className="px-1 py-0.5 rounded bg-emerald-900 text-emerald-200 text-[9px] font-medium">
                  LOCAL
                </span>
              ) : (
                <span className="px-1 py-0.5 rounded bg-amber-900 text-amber-200 text-[9px] font-medium">
                  CLOUD
                </span>
              )}
              {r.profile && (
                <span
                  className="px-1 py-0.5 rounded bg-zinc-800 text-zinc-300 text-[9px] font-medium"
                  title="Profile active when this decision was made"
                >
                  {r.profile}
                </span>
              )}
            </div>
            <span className="text-[10px] text-zinc-500">
              {new Date(r.created_at).toLocaleTimeString()}
            </span>
          </div>
          <div className="mt-1 text-zinc-400 text-[11px]">{r.reason}</div>
          <div className="mt-1 text-[10px] text-zinc-500 italic">
            sent: <span className="text-zinc-400">{r.scrubbed_preview}</span>
          </div>
          <div className="mt-1 text-[10px] text-zinc-500 flex gap-3">
            <span title="classifier + routing">
              route <span className="text-zinc-300">{fmtMs(r.t_classify_ms)}</span>
            </span>
            <span title="time from routing to first token">
              ttft <span className="text-zinc-300">{fmtMs(r.t_first_token_ms)}</span>
            </span>
            <span title="total turn duration">
              total <span className="text-zinc-300">{fmtMs(r.t_total_ms)}</span>
            </span>
            <span className="ml-auto text-zinc-600">
              {(r.classification?.source as string) ?? ""}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}
