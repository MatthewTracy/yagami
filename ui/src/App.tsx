import { useState } from "react";
import { Chat } from "./components/Chat";
import { CostMeter } from "./components/CostMeter";
import { DebugPanel } from "./components/DebugPanel";
import { PrivacyLedger } from "./components/PrivacyLedger";
import { ConversationsSidebar } from "./components/ConversationsSidebar";
import { ShortcutSheet } from "./components/ShortcutSheet";
import { ToastHost } from "./components/Toast";

type Routing = {
  backend: string;
  isLocal: boolean;
  reason: string;
  classification: Record<string, unknown>;
};

export default function App() {
  const [routing, setRouting] = useState<Routing | undefined>();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [loadSessionId, setLoadSessionId] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  function newChat() {
    setLoadSessionId(null);
    setRouting(undefined);
    window.location.reload();
  }

  return (
    <div className="h-screen grid grid-cols-[220px_1fr_320px] overflow-hidden">
      <aside className="border-r border-zinc-800 min-h-0 overflow-hidden">
        <ConversationsSidebar
          activeSessionId={sessionId}
          refreshKey={refreshKey}
          onSelect={(id) => setLoadSessionId(id)}
          onNew={newChat}
          onChange={() => setRefreshKey((k) => k + 1)}
        />
      </aside>
      <div className="border-r border-zinc-800 flex flex-col min-h-0 overflow-hidden">
        <header className="px-4 py-3 border-b border-zinc-800 flex items-center gap-2 shrink-0">
          <span className="font-semibold tracking-tight">Yagami</span>
          <span className="text-xs text-zinc-500">local-first AI orchestrator</span>
        </header>
        <Chat
          loadSessionId={loadSessionId}
          onRouting={setRouting}
          onSession={(id) => setSessionId(id)}
          onTurnComplete={() => setRefreshKey((k) => k + 1)}
        />
      </div>
      <aside className="p-3 space-y-4 overflow-y-auto min-h-0">
        <section>
          <div className="text-xs uppercase tracking-wider text-zinc-500 mb-2">Cost</div>
          <CostMeter sessionId={sessionId} refreshKey={refreshKey} />
        </section>
        <section>
          <div className="text-xs uppercase tracking-wider text-zinc-500 mb-2">Routing (current)</div>
          <DebugPanel
            backend={routing?.backend}
            isLocal={routing?.isLocal}
            reason={routing?.reason}
            classification={routing?.classification}
          />
        </section>
        <section>
          <div className="text-xs uppercase tracking-wider text-zinc-500 mb-2">Privacy Ledger</div>
          <PrivacyLedger sessionId={sessionId} refreshKey={refreshKey} />
        </section>
      </aside>
      <ToastHost />
      <ShortcutSheet />
    </div>
  );
}
