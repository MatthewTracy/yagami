import { useEffect, useState } from "react";

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
};

export function ConversationsSidebar({ activeSessionId, refreshKey, onSelect, onNew }: Props) {
  const [sessions, setSessions] = useState<SessionRow[]>([]);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/sessions?limit=100")
      .then((r) => r.json())
      .then((d) => {
        if (!cancelled) setSessions(d.sessions || []);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

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
          return (
            <button
              key={s.id}
              onClick={() => onSelect(s.id)}
              className={`w-full text-left text-xs px-2 py-1.5 rounded truncate ${
                active ? "bg-zinc-700 text-zinc-100" : "text-zinc-400 hover:bg-zinc-800"
              }`}
              title={s.title ?? s.id}
            >
              {s.title ?? `(empty) ${s.id.slice(0, 8)}`}
            </button>
          );
        })}
      </div>
    </div>
  );
}
