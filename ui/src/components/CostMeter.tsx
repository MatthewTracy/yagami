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

  useEffect(() => {
    let cancelled = false;
    const url = sessionId ? `/api/costs?session_id=${sessionId}` : "/api/costs";
    fetchJson<Costs>(url)
      .then((d) => {
        if (!cancelled) setC(d);
      })
      .catch(() => {
        if (!cancelled) setC(null);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, refreshKey]);

  if (!c) return null;
  const tone = c.cap_exceeded
    ? "bg-red-900/20 border-red-900/50 text-red-200"
    : c.daily_cap_usd > 0 && c.today_usd / c.daily_cap_usd > 0.8
      ? "bg-amber-900/20 border-amber-900/50 text-amber-200"
      : "bg-zinc-900/40 border-zinc-800 text-zinc-300";
  return (
    <div className={`text-xs px-2 py-1.5 rounded border ${tone}`}>
      <div className="flex justify-between">
        <span>today</span>
        <span className="font-mono">{fmtUsd(c.today_usd)}</span>
      </div>
      <div className="flex justify-between">
        <span>session</span>
        <span className="font-mono">{fmtUsd(c.session_usd)}</span>
      </div>
      {c.daily_cap_usd > 0 && (
        <div className="flex justify-between text-[10px] text-zinc-500 mt-0.5">
          <span>cap</span>
          <span>
            {fmtUsd(c.daily_cap_usd)}
            {c.cap_exceeded ? " - cloud blocked" : ""}
          </span>
        </div>
      )}
    </div>
  );
}
