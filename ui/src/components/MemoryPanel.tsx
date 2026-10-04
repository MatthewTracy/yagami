import { useEffect, useRef, useState } from "react";
import { Dialog } from "./Dialog";
import { emitToast } from "./Toast";
import { fetchJson } from "../lib/http";

type Observation = {
  id: number;
  session_id: string;
  role: string;
  text: string;
  sensitivity: string;
  source_app?: string;
  ttl_until?: number | null;
  created_at: number;
  embedding_status: string;
  chunk_index?: number;
  parent_id?: number | null;
};

type Stats = {
  total: number;
  vec_total: number;
  by_status: Record<string, number>;
};

type Props = {
  open: boolean;
  onClose: () => void;
};

function fmtDate(ms: number): string {
  return new Date(ms).toLocaleString();
}

function sensColor(s: string): string {
  if (s === "phi" || s === "phi_medical") return "bg-amber-900 text-amber-100";
  if (s === "secret") return "bg-red-900 text-red-100";
  return "bg-zinc-800 text-zinc-300";
}

export function MemoryPanel({ open, onClose }: Props) {
  const [items, setItems] = useState<Observation[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [searching, setSearching] = useState(false);
  const [failed, setFailed] = useState(false);
  const requestId = useRef(0);

  async function refresh() {
    const current = ++requestId.current;
    setLoading(true);
    setSearching(false);
    setFailed(false);
    try {
      const [observations, summary] = await Promise.all([
        fetchJson<{ observations?: Observation[] }>("/api/memory?limit=100"),
        fetchJson<Stats>("/api/memory/stats"),
      ]);
      if (current !== requestId.current) return;
      setItems(observations.observations || []);
      setStats(summary);
    } catch {
      if (current === requestId.current) setFailed(true);
    } finally {
      if (current === requestId.current) setLoading(false);
    }
  }

  useEffect(() => {
    if (!open) return;
    refresh();
    return () => {
      requestId.current += 1;
    };
  }, [open]);

  if (!open) return null;

  async function doSearch(e?: React.FormEvent) {
    e?.preventDefault();
    if (!query.trim()) {
      refresh();
      return;
    }
    const current = ++requestId.current;
    setSearching(true);
    setLoading(false);
    setFailed(false);
    try {
      const result = await fetchJson<{ observations?: Observation[] }>(
        `/api/memory/search?q=${encodeURIComponent(query.trim())}&limit=50`,
      );
      if (current === requestId.current) setItems(result.observations || []);
    } catch {
      if (current === requestId.current) setFailed(true);
    } finally {
      if (current === requestId.current) setSearching(false);
    }
  }

  async function deleteOne(id: number) {
    try {
      const r = await fetch(`/api/memory/${id}`, { method: "DELETE" });
      if (!r.ok) {
        emitToast("error", `Delete failed: ${r.status}`);
        return;
      }
      setItems((cur) => cur.filter((x) => x.id !== id));
    } catch {
      emitToast("error", "Could not delete observation. Please try again.");
    }
  }

  return (
    <Dialog title="Cross-session memory" onClose={onClose}>
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-zinc-100">
          Cross-session memory
        </h3>
        <button
          onClick={onClose}
          className="text-zinc-400 hover:text-white text-lg leading-none"
          aria-label="Close"
        >
          ×
        </button>
      </div>

      {stats && (
        <div className="grid grid-cols-3 gap-2 mb-3 text-xs">
          <Tile label="Total observations" value={String(stats.total)} />
          <Tile label="With embeddings" value={String(stats.vec_total)} />
          <Tile
            label="Pending"
            value={String(stats.by_status.pending || 0)}
            tone={stats.by_status.pending ? "amber" : "neutral"}
          />
        </div>
      )}

      <form onSubmit={doSearch} className="mb-3 flex gap-2">
        <input
          aria-label="Search memory"
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search memory (FTS keyword)…"
          className="flex-1 bg-zinc-950 border border-zinc-800 rounded px-2 py-1.5 text-sm text-zinc-200"
        />
        <button
          type="submit"
          disabled={searching || loading}
          className="px-3 py-1.5 text-xs rounded bg-zinc-700 hover:bg-zinc-600 text-white disabled:opacity-40"
        >
          {searching ? "…" : "Search"}
        </button>
        <button
          type="button"
          onClick={() => {
            setQuery("");
            refresh();
          }}
          className="px-3 py-1.5 text-xs rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300"
        >
          All
        </button>
      </form>

      {failed ? (
        <div role="alert" className="text-zinc-300 text-sm py-6 text-center">
          Memory could not be loaded.
          <button className="block mx-auto mt-3 underline" onClick={refresh}>
            Try again
          </button>
        </div>
      ) : loading && items.length === 0 ? (
        <div className="text-zinc-400 text-sm py-6 text-center">Loading…</div>
      ) : items.length === 0 ? (
        <div className="text-zinc-400 text-sm py-6 text-center">
          {query.trim()
            ? "No matching observations."
            : "No observations yet. Conversation memory will appear here when available."}
        </div>
      ) : (
        <div className="space-y-1.5 max-h-[60vh] overflow-y-auto pr-1">
          {items.map((o) => (
            <div
              key={o.id}
              className="text-xs p-2 rounded border border-zinc-800 bg-zinc-950/40 group"
            >
              <div className="flex items-center gap-2 mb-1">
                <span className="font-mono text-zinc-400 text-[10px]">
                  #{o.id}
                </span>
                <span className="font-mono text-zinc-400 text-[10px]">
                  {o.role}
                </span>
                <span
                  className={`px-1 py-0.5 rounded text-[9px] font-medium ${sensColor(o.sensitivity)}`}
                >
                  {o.sensitivity}
                </span>
                <span className="text-[10px] text-zinc-400 font-mono">
                  {o.embedding_status}
                </span>
                <span className="ml-auto text-[10px] text-zinc-400">
                  {fmtDate(o.created_at)}
                </span>
                <button
                  onClick={() => deleteOne(o.id)}
                  className="text-zinc-400 hover:text-red-400 opacity-100 sm:opacity-0 group-hover:opacity-100 group-focus-within:opacity-100"
                  aria-label={`Delete observation ${o.id}`}
                  title="Delete"
                >
                  ×
                </button>
              </div>
              <div className="text-zinc-300 break-words whitespace-pre-wrap">
                {o.text}
              </div>
            </div>
          ))}
        </div>
      )}
    </Dialog>
  );
}

function Tile({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "neutral" | "amber";
}) {
  const cls =
    tone === "amber"
      ? "border-amber-900/50 bg-amber-900/10 text-amber-200"
      : "border-zinc-800 bg-zinc-950/40 text-zinc-200";
  return (
    <div className={`p-2 rounded border ${cls}`}>
      <div className="text-[10px] uppercase tracking-wider text-zinc-400">
        {label}
      </div>
      <div className="text-lg font-semibold font-mono">{value}</div>
    </div>
  );
}
