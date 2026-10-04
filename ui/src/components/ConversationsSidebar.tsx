import { useEffect, useRef, useState } from "react";
import { fetchJson } from "../lib/http";
import { emitToast } from "./Toast";
import { Icon } from "./Icon";

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

export function ConversationsSidebar({
  activeSessionId,
  refreshKey,
  onSelect,
  onNew,
  onChange,
}: Props) {
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [failed, setFailed] = useState(false);
  const renaming = useRef(new Set<string>());
  const cancelledEdit = useRef(false);

  function refresh() {
    fetchJson<{ sessions?: SessionRow[] }>("/api/sessions?limit=100")
      .then((d) => {
        setSessions(d.sessions || []);
        setFailed(false);
      })
      .catch(() => setFailed(true));
  }

  useEffect(() => {
    let cancelled = false;
    fetchJson<{ sessions?: SessionRow[] }>("/api/sessions?limit=100")
      .then((d) => {
        if (!cancelled) {
          setSessions(d.sessions || []);
          setFailed(false);
        }
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  async function commitRename(id: string) {
    if (cancelledEdit.current || renaming.current.has(id)) return;
    const title = editValue.trim();
    setEditingId(null);
    if (!title) return;
    const cur = sessions.find((s) => s.id === id);
    if (cur && cur.title === title) return;
    renaming.current.add(id);
    try {
      const response = await fetch(`/api/sessions/${encodeURIComponent(id)}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ title }),
      });
      if (!response.ok) throw new Error("rename failed");
      refresh();
      onChange();
    } catch {
      emitToast("error", "Could not rename conversation. Please try again.");
    } finally {
      renaming.current.delete(id);
    }
  }

  async function deleteSession(id: string) {
    if (!confirm("Delete this conversation? This cannot be undone.")) return;
    try {
      const response = await fetch(`/api/sessions/${encodeURIComponent(id)}`, {
        method: "DELETE",
      });
      if (!response.ok) throw new Error("delete failed");
      refresh();
      onChange();
      if (id === activeSessionId) onNew();
    } catch {
      emitToast("error", "Could not delete conversation. Please try again.");
    }
  }

  return (
    <div className="h-full min-h-0 flex flex-col">
      <div className="conversation-heading">Workspace</div>
      <div className="px-4 pb-4 border-b border-zinc-800">
        <button onClick={onNew} className="new-chat">
          <Icon name="plus" size={16} /> New chat
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-2 space-y-1">
        <div className="section-label px-2 pt-3">Recent conversations</div>
        {failed && (
          <div role="alert" className="history-empty">
            Conversation history is unavailable.
            <button onClick={refresh} className="block mt-2 underline">
              Try again
            </button>
          </div>
        )}
        {!failed && sessions.length === 0 && (
          <div className="history-empty">
            <strong>No conversations yet.</strong>Your recent chats will appear
            here.
          </div>
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
                  aria-label="Conversation title"
                  className="flex-1 min-w-0 bg-zinc-950 border border-zinc-700 rounded px-1 py-0.5 text-xs text-zinc-100 outline-none focus:border-zinc-500"
                  value={editValue}
                  onChange={(e) => setEditValue(e.target.value)}
                  onBlur={() => commitRename(s.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") commitRename(s.id);
                    if (e.key === "Escape") {
                      cancelledEdit.current = true;
                      setEditingId(null);
                    }
                  }}
                />
              ) : (
                <button
                  onClick={() => onSelect(s.id)}
                  aria-current={active ? "page" : undefined}
                  className={`flex-1 min-w-0 text-left text-xs truncate py-1 ${
                    active ? "text-zinc-100" : "text-zinc-400"
                  }`}
                  title={s.title ?? s.id}
                >
                  {s.title ?? `(empty) ${s.id.slice(0, 8)}`}
                </button>
              )}
              {!isEditing && (
                <div className="opacity-100 sm:opacity-0 sm:group-hover:opacity-100 group-focus-within:opacity-100 flex gap-0.5 transition-opacity">
                  <button
                    onClick={() => {
                      setEditingId(s.id);
                      cancelledEdit.current = false;
                      setEditValue(s.title ?? "");
                    }}
                    title="Rename"
                    aria-label={`Rename ${s.title ?? "conversation"}`}
                    className="px-1 text-zinc-400 hover:text-zinc-200 text-xs"
                  >
                    ✎
                  </button>
                  <button
                    onClick={() => deleteSession(s.id)}
                    title="Delete"
                    aria-label={`Delete ${s.title ?? "conversation"}`}
                    className="px-1 text-zinc-400 hover:text-red-400 text-xs"
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
