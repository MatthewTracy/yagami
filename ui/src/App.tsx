import { useState } from "react";
import { Chat } from "./components/Chat";
import { DebugPanel } from "./components/DebugPanel";

type Routing = {
  backend: string;
  isLocal: boolean;
  reason: string;
  classification: Record<string, unknown>;
};

export default function App() {
  const [routing, setRouting] = useState<Routing | undefined>();
  return (
    <div className="h-full grid grid-cols-[1fr_320px]">
      <div className="border-r border-zinc-800 flex flex-col">
        <header className="px-4 py-3 border-b border-zinc-800 flex items-center gap-2">
          <span className="font-semibold tracking-tight">Yagami</span>
          <span className="text-xs text-zinc-500">local-first AI orchestrator</span>
        </header>
        <Chat onRouting={setRouting} />
      </div>
      <aside className="p-3 space-y-3">
        <div className="text-xs uppercase tracking-wider text-zinc-500">Routing</div>
        <DebugPanel
          backend={routing?.backend}
          isLocal={routing?.isLocal}
          reason={routing?.reason}
          classification={routing?.classification}
        />
      </aside>
    </div>
  );
}
