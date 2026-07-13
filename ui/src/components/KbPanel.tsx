import { useEffect, useState } from "react";
import { emitToast } from "./Toast";

type Source = {
  source_path: string;
  chunks: number;
  indexed_at: number;
};

type Props = {
  open: boolean;
  onClose: () => void;
};

export function KbPanel({ open, onClose }: Props) {
  const [sources, setSources] = useState<Source[]>([]);
  const [path, setPath] = useState("");
  const [loading, setLoading] = useState(false);
  const [indexing, setIndexing] = useState(false);

  async function refresh() {
    setLoading(true);
    try {
      const r = await fetch("/api/kb");
      setSources((await r.json()).sources || []);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!open) return;
    refresh();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  if (!open) return null;

  async function doIndex(e?: React.FormEvent) {
    e?.preventDefault();
    const folder = path.trim();
    if (!folder) return;
    setIndexing(true);
    try {
      const r = await fetch("/api/kb/index", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: folder }),
      });
      if (!r.ok) {
        emitToast("error", `Index failed (${r.status}): ${await r.text()}`);
        return;
      }
      const d = await r.json();
      emitToast(
        "info",
        `Indexed ${d.files_indexed} file(s), ${d.chunks_written} chunk(s)` +
          (d.files_skipped ? `, ${d.files_skipped} skipped` : ""),
      );
      setPath("");
      refresh();
    } finally {
      setIndexing(false);
    }
  }

  async function deleteSource(sourcePath: string) {
    const r = await fetch(`/api/kb/source?path=${encodeURIComponent(sourcePath)}`, {
      method: "DELETE",
    });
    if (!r.ok) {
      emitToast("error", `Delete failed: ${r.status}`);
      return;
    }
    setSources((cur) => cur.filter((s) => s.source_path !== sourcePath));
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 overflow-y-auto py-8"
      onClick={onClose}
    >
      <div
        className="bg-zinc-900 border border-zinc-700 rounded-lg p-5 max-w-3xl w-full mx-4 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-zinc-100">Knowledge base (indexed documents)</h3>
          <button
            onClick={onClose}
            className="text-zinc-400 hover:text-white text-lg leading-none"
            aria-label="Close"
          >
            ×
          </button>
        </div>

        <form onSubmit={doIndex} className="mb-2 flex gap-2">
          <input
            type="text"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="Folder to index, e.g. C:\Users\you\Documents\project-docs"
            className="flex-1 bg-zinc-950 border border-zinc-800 rounded px-2 py-1.5 text-sm text-zinc-200 font-mono"
          />
          <button
            type="submit"
            disabled={indexing || !path.trim()}
            className="px-3 py-1.5 text-xs rounded bg-emerald-700 hover:bg-emerald-600 text-white disabled:opacity-40"
          >
            {indexing ? "Indexing…" : "Index"}
          </button>
        </form>
        <p className="text-[10px] text-zinc-500 mb-3">
          Recursively indexes .pdf / .md / .txt / .log. Re-indexing a folder
          replaces each file's chunks. Embedding runs inside this request -
          a big folder takes a while. The kb.recall skill searches what's
          indexed here; results can reach the cloud backend driving a
          tool-use turn, so don't index anything you wouldn't send there.
        </p>

        {loading && sources.length === 0 ? (
          <div className="text-zinc-400 text-sm py-6 text-center">Loading…</div>
        ) : sources.length === 0 ? (
          <div className="text-zinc-400 text-sm py-6 text-center">
            Nothing indexed yet. Point it at a folder of docs above.
          </div>
        ) : (
          <div className="space-y-1.5 max-h-[60vh] overflow-y-auto pr-1">
            {sources.map((s) => (
              <div
                key={s.source_path}
                className="text-xs p-2 rounded border border-zinc-800 bg-zinc-950/40 group flex items-center gap-2"
              >
                <span className="font-mono text-zinc-300 break-all flex-1 min-w-0">
                  {s.source_path}
                </span>
                <span className="text-[10px] text-zinc-500 shrink-0">
                  {s.chunks} chunk{s.chunks === 1 ? "" : "s"}
                </span>
                <span className="text-[10px] text-zinc-600 shrink-0">
                  {new Date(s.indexed_at).toLocaleString()}
                </span>
                <button
                  onClick={() => deleteSource(s.source_path)}
                  className="text-zinc-500 hover:text-red-400 opacity-0 group-hover:opacity-100 shrink-0"
                  title="Remove from index"
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
