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
};

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

  return (
    <div className="space-y-2">
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
            </div>
            <span className="text-[10px] text-zinc-500">
              {new Date(r.created_at).toLocaleTimeString()}
            </span>
          </div>
          <div className="mt-1 text-zinc-400 text-[11px]">{r.reason}</div>
          <div className="mt-1 text-[10px] text-zinc-500 italic">
            sent: <span className="text-zinc-400">{r.scrubbed_preview}</span>
          </div>
        </div>
      ))}
    </div>
  );
}
