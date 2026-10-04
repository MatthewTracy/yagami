import { useEffect, useState } from "react";
import { fetchJson } from "../lib/http";

type Costs = {
  today_usd: number;
  session_usd: number;
  daily_cap_usd: number;
  cap_remaining_usd: number | null;
  cap_exceeded: boolean;
};

type Props = { sessionId: string | null; refreshKey: number };

function fmtUsd(n: number): string {
  if (n < 0.01) return `$${n.toFixed(4)}`;
  return `$${n.toFixed(2)}`;
}

export function CostMeter({ sessionId, refreshKey }: Props) {
  const [c, setC] = useState<Costs | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setC(null);
    const url = sessionId
      ? `/api/costs?session_id=${encodeURIComponent(sessionId)}`
      : "/api/costs";
    fetchJson<Costs>(url)
      .then((d) => {
        if (!cancelled) setC(d);
      })
      .catch(() => {
        if (!cancelled) setC(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, refreshKey]);

  if (!c)
    return (
      <div className="text-xs text-zinc-400 p-3 rounded-lg border border-zinc-800">
        {loading ? "Loading usage…" : "Usage is unavailable."}
      </div>
    );
  const tone = c.cap_exceeded
    ? "bg-red-900/20 border-red-900/50 text-red-200"
    : c.daily_cap_usd > 0 && c.today_usd / c.daily_cap_usd > 0.8
      ? "bg-amber-900/20 border-amber-900/50 text-amber-200"
      : "bg-zinc-900/40 border-zinc-800 text-zinc-300";
  return (
    <div className={`text-xs p-3 rounded-lg border space-y-2 ${tone}`}>
      <div className="flex justify-between">
        <span>Today</span>
        <span className="font-mono">{fmtUsd(c.today_usd)}</span>
      </div>
      <div className="flex justify-between">
        <span>This session</span>
        <span className="font-mono">{fmtUsd(c.session_usd)}</span>
      </div>
      {c.daily_cap_usd > 0 && (
        <>
          <progress
            className="w-full h-1.5 accent-emerald-500"
            aria-label="Daily budget used"
            max={c.daily_cap_usd}
            value={Math.min(c.daily_cap_usd, Math.max(0, c.today_usd))}
          />
          <div className="flex justify-between text-[10px] text-zinc-400 mt-0.5">
            <span>Daily limit</span>
            <span>
              {fmtUsd(c.daily_cap_usd)}
              {c.cap_exceeded ? " - cloud blocked" : ""}
            </span>
          </div>
        </>
      )}
    </div>
  );
}
