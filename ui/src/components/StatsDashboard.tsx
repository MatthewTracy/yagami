import { useEffect, useState } from "react";
import { Dialog } from "./Dialog";
import { fetchJson } from "../lib/http";

type Stats = {
  window_days: number;
  total_turns: number;
  total_cost_usd: number;
  by_backend: {
    backend: string;
    turns: number;
    cost_usd: number;
    tokens_in: number;
    tokens_out: number;
    avg_ttft_ms: number | null;
    avg_total_ms: number | null;
  }[];
  by_day: { day: string; cost_usd: number; turns: number }[];
  by_classification_source: { source: string; turns: number }[];
};

type Props = {
  open: boolean;
  onClose: () => void;
};

function fmtUsd(n: number): string {
  if (n === 0) return "$0";
  if (n < 0.01) return `$${n.toFixed(4)}`;
  return `$${n.toFixed(2)}`;
}

function fmtMs(n: number | null): string {
  if (n == null) return "-";
  if (n < 1000) return `${n}ms`;
  return `${(n / 1000).toFixed(1)}s`;
}

export function StatsDashboard({ open, onClose }: Props) {
  const [data, setData] = useState<Stats | null>(null);
  const [days, setDays] = useState(14);
  const [failed, setFailed] = useState(false);
  const [retryKey, setRetryKey] = useState(0);

  useEffect(() => {
    if (!open) return;
    let active = true;
    setData(null);
    setFailed(false);
    fetchJson<Stats>(`/api/stats?days=${days}`)
      .then((d) => {
        if (active) setData(d);
      })
      .catch(() => {
        if (active) setFailed(true);
      });
    return () => {
      active = false;
    };
  }, [open, days, retryKey]);

  if (!open) return null;
  const maxDay = data
    ? Math.max(0.001, ...data.by_day.map((d) => d.cost_usd))
    : 1;
  const maxTurns = data
    ? Math.max(1, ...data.by_backend.map((b) => b.turns))
    : 1;

  return (
    <Dialog title="Stats" onClose={onClose}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-3">
          <h3 className="text-sm font-semibold text-zinc-100">Stats</h3>
          <select
            aria-label="Statistics period"
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="bg-zinc-950 border border-zinc-800 rounded px-2 py-0.5 text-zinc-200 text-[11px]"
          >
            {[1, 7, 14, 30, 90].map((d) => (
              <option key={d} value={d}>
                last {d} days
              </option>
            ))}
          </select>
        </div>
        <button
          onClick={onClose}
          className="text-zinc-400 hover:text-white text-lg leading-none"
          aria-label="Close"
        >
          ×
        </button>
      </div>

      {failed ? (
        <div role="alert" className="text-sm text-zinc-300">
          Statistics could not be loaded.
          <button
            className="block mt-3 underline"
            onClick={() => setRetryKey((key) => key + 1)}
          >
            Try again
          </button>
        </div>
      ) : !data ? (
        <div className="text-zinc-300 text-sm">Loading…</div>
      ) : data.total_turns === 0 ? (
        <div className="text-zinc-400 text-sm py-6 text-center">
          No turns yet in the selected window.
        </div>
      ) : (
        <div className="space-y-4 text-xs">
          <div className="grid grid-cols-2 gap-3">
            <Tile
              label="Total turns"
              value={data.total_turns.toLocaleString()}
              sub={`${data.window_days}-day window`}
            />
            <Tile
              label="Total spend"
              value={fmtUsd(data.total_cost_usd)}
              sub="cloud backends only"
            />
          </div>

          <Group title="By backend">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="text-[10px] uppercase tracking-wider text-zinc-400 text-left">
                    <th className="py-1">Backend</th>
                    <th className="py-1">Turns</th>
                    <th className="py-1">Cost</th>
                    <th className="py-1">TTFT</th>
                    <th className="py-1">Total</th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_backend.map((b) => (
                    <tr key={b.backend} className="border-t border-zinc-800/60">
                      <td className="py-1 font-mono text-zinc-200">
                        {b.backend}
                      </td>
                      <td className="py-1">
                        <div className="flex items-center gap-2">
                          <div className="w-24 h-1.5 bg-zinc-800 rounded overflow-hidden">
                            <div
                              className="h-full bg-emerald-700"
                              style={{
                                width: `${(b.turns / maxTurns) * 100}%`,
                              }}
                            />
                          </div>
                          <span className="text-zinc-300">{b.turns}</span>
                        </div>
                      </td>
                      <td className="py-1 text-zinc-300 font-mono">
                        {fmtUsd(b.cost_usd)}
                      </td>
                      <td className="py-1 text-zinc-400">
                        {fmtMs(b.avg_ttft_ms)}
                      </td>
                      <td className="py-1 text-zinc-400">
                        {fmtMs(b.avg_total_ms)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Group>

          <Group title={`Daily spend (${data.by_day.length} days)`}>
            <div className="flex items-end gap-1 h-20">
              {data.by_day.map((d) => (
                <div
                  key={d.day}
                  title={`${d.day}: ${fmtUsd(d.cost_usd)} · ${d.turns} turns`}
                  className="flex-1 min-w-0 flex flex-col items-center gap-1"
                >
                  <div
                    className="w-full bg-emerald-700/70 hover:bg-emerald-600 rounded-t"
                    style={{
                      height: `${(d.cost_usd / maxDay) * 100}%`,
                      minHeight: d.cost_usd > 0 ? "2px" : "0",
                    }}
                  />
                  <div className="text-[9px] text-zinc-600 truncate w-full text-center">
                    {d.day.slice(5)}
                  </div>
                </div>
              ))}
            </div>
          </Group>

          <Group title="Routing source">
            <div className="space-y-1">
              {data.by_classification_source.map((s) => (
                <div key={s.source} className="flex items-center gap-2">
                  <span className="text-zinc-400 w-48 truncate font-mono text-[10px]">
                    {s.source}
                  </span>
                  <div className="flex-1 h-1.5 bg-zinc-800 rounded overflow-hidden">
                    <div
                      className="h-full bg-zinc-500"
                      style={{
                        width: `${(s.turns / data.total_turns) * 100}%`,
                      }}
                    />
                  </div>
                  <span className="text-zinc-300 w-10 text-right">
                    {s.turns}
                  </span>
                </div>
              ))}
            </div>
          </Group>
        </div>
      )}
    </Dialog>
  );
}

function Tile({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub: string;
}) {
  return (
    <div className="p-3 rounded border border-zinc-800 bg-zinc-950/40">
      <div className="text-[10px] uppercase tracking-wider text-zinc-400">
        {label}
      </div>
      <div className="text-xl font-semibold text-zinc-100 font-mono">
        {value}
      </div>
      <div className="text-[10px] text-zinc-400 italic">{sub}</div>
    </div>
  );
}

function Group({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-2 p-2.5 rounded border border-zinc-800 bg-zinc-950/30">
      <div className="text-[10px] uppercase tracking-wider text-zinc-400">
        {title}
      </div>
      {children}
    </div>
  );
}
