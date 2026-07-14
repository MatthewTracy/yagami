import { useEffect, useState } from "react";
import { fetchJson } from "../lib/http";

type SessionRow = {
  id: string;
  created_at: number;
  updated_at: number;
  title: string | null;
};

type Props = {
  activeSessionId: string | null;
  refreshKey: number;
  onSelect: (sessionId: string) => void;
  onNew: () => void;
  onChange: () => void;
};

export function ConversationsSidebar({ activeSessionId, refreshKey, onSelect, onNew, onChange }: Props) {
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");

  function refresh() {
    fetchJson<{ sessions?: SessionRow[] }>("/api/sessions?limit=100")
      .then((d) => setSessions(d.sessions || []))
      .catch(() => setSessions([]));
  }

  useEffect(() => {
    let cancelled = false;
    fetchJson<{ sessions?: SessionRow[] }>("/api/sessions?limit=100")
      .then((d) => {
        if (!cancelled) setSessions(d.sessions || []);
      })
      .catch(() => {
        if (!cancelled) setSessions([]);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  async function commitRename(id: string) {
    const title = editValue.trim();
    setEditingId(null);
    if (!title) return;
    const cur = sessions.find((s) => s.id === id);
    if (cur && cur.title === title) return;
    await fetch(`/api/sessions/${id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ title }),
    });
    refresh();
    onChange();
  }

  async function deleteSession(id: string) {
    if (!confirm("Delete this conversation? This cannot be undone.")) return;
    await fetch(`/api/sessions/${id}`, { method: "DELETE" });
    refresh();
    onChange();
  }

  return (
    <div className="h-full flex flex-col">
      <div className="px-3 py-3 border-b border-zinc-800">
        <button
          onClick={onNew}
          className="w-full text-sm bg-zinc-800 hover:bg-zinc-700 rounded-md py-1.5"
        >
          + New chat
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-2 space-y-1">
        {sessions.length === 0 && (
          <div className="text-xs text-zinc-500 px-2 py-3">No conversations yet.</div>
        )}
        {sessions.map((s) => {
          const active = s.id === activeSessionId;
          const isEditing = editingId === s.id;
          return (
            <div
              key={s.id}
              className={`group flex items-center gap-1 px-2 py-1.5 rounded ${
                active ? "bg-zinc-700" : "hover:bg-zinc-800"
              }`}
            >
              {isEditing ? (
                <input
                  autoFocus
                  className="flex-1 bg-zinc-950 border border-zinc-700 rounded px-1 py-0.5 text-xs text-zinc-100 outline-none focus:border-zinc-500"
                  value={editValue}
                  onChange={(e) => setEditValue(e.target.value)}
                  onBlur={() => commitRename(s.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") commitRename(s.id);
                    if (e.key === "Escape") setEditingId(null);
                  }}
                />
              ) : (
                <button
                  onClick={() => onSelect(s.id)}
                  className={`flex-1 text-left text-xs truncate ${
                    active ? "text-zinc-100" : "text-zinc-400"
                  }`}
                  title={s.title ?? s.id}
                >
                  {s.title ?? `(empty) ${s.id.slice(0, 8)}`}
                </button>
              )}
              {!isEditing && (
                <div className="opacity-0 group-hover:opacity-100 flex gap-0.5 transition-opacity">
                  <button
                    onClick={() => {
                      setEditingId(s.id);
                      setEditValue(s.title ?? "");
                    }}
                    title="Rename"
                    className="px-1 text-zinc-500 hover:text-zinc-200 text-xs"
                  >
                    ✎
                  </button>
                  <button
                    onClick={() => deleteSession(s.id)}
                    title="Delete"
                    className="px-1 text-zinc-500 hover:text-red-400 text-xs"
                  >
                    ×
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
