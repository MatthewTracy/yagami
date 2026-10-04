import { useCallback, useEffect, useState } from "react";
import { Chat } from "./components/Chat";
import { CostMeter } from "./components/CostMeter";
import { DebugPanel } from "./components/DebugPanel";
import { PrivacyLedger } from "./components/PrivacyLedger";
import { ConversationsSidebar } from "./components/ConversationsSidebar";
import { MemoryPanel } from "./components/MemoryPanel";
import { SettingsModal } from "./components/SettingsModal";
import { ShortcutSheet } from "./components/ShortcutSheet";
import { StatsDashboard } from "./components/StatsDashboard";
import { ToastHost } from "./components/Toast";
import { Dialog } from "./components/Dialog";
import { Icon } from "./components/Icon";
import { fetchJson } from "./lib/http";

type Routing = {
  backend: string;
  isLocal: boolean;
  reason: string;
  classification: Record<string, unknown>;
};
type Health = { demo_mode?: boolean; default_backend?: string; mode?: string };
type Panel =
  | "settings"
  | "stats"
  | "memory"
  | "conversations"
  | "details"
  | null;

export default function App() {
  const [routing, setRouting] = useState<Routing>();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [loadSessionId, setLoadSessionId] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [chatKey, setChatKey] = useState(0);
  const [panel, setPanel] = useState<Panel>(null);
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    let active = true;
    fetchJson<Health>("/api/health")
      .then((data) => {
        if (active) setHealth(data);
      })
      .catch(() => {
        if (active) setHealth(null);
      });
    return () => {
      active = false;
    };
  }, []);

  const newChat = useCallback(() => {
    try {
      sessionStorage.removeItem("yagami:draft");
    } catch {
      /* storage can be disabled */
    }
    setLoadSessionId(null);
    setSessionId(null);
    setRouting(undefined);
    setChatKey((key) => key + 1);
    setPanel(null);
  }, []);
  useEffect(() => {
    window.addEventListener("yagami:new-chat", newChat);
    return () => window.removeEventListener("yagami:new-chat", newChat);
  }, [newChat]);

  function selectSession(id: string) {
    setRouting(undefined);
    setLoadSessionId(id);
    setChatKey((key) => key + 1);
    setPanel(null);
  }
  const sidebar = (
    <ConversationsSidebar
      activeSessionId={sessionId}
      refreshKey={refreshKey}
      onSelect={selectSession}
      onNew={newChat}
      onChange={() => setRefreshKey((key) => key + 1)}
    />
  );
  const details = (
    <div className="insights-content">
      <div className="section-heading">
        <span>Session insights</span>
        <Icon name="shield" size={16} />
      </div>
      <section aria-label="Spend">
        <h2 className="section-label">Usage & budget</h2>
        <CostMeter sessionId={sessionId} refreshKey={refreshKey} />
      </section>
      <section aria-label="Current routing">
        <h2 className="section-label">Current route</h2>
        <DebugPanel
          backend={routing?.backend}
          isLocal={routing?.isLocal}
          reason={routing?.reason}
          classification={routing?.classification}
        />
      </section>
      <section aria-label="Privacy ledger">
        <h2 className="section-label">Privacy ledger</h2>
        <PrivacyLedger sessionId={sessionId} refreshKey={refreshKey} />
      </section>
      <p className="insights-note">
        Review where each turn was routed and the policy decision behind it.
      </p>
    </div>
  );

  return (
    <div className="app-shell">
      <a href="#message-input" className="skip-link">
        Skip to message input
      </a>
      <aside className="conversation-rail" aria-label="Conversation history">
        {sidebar}
      </aside>
      <main className="chat-workspace" aria-label="Chat workspace">
        <header className="workspace-header">
          <button
            className="toolbar-button mobile-nav"
            aria-label="Open conversations"
            onClick={(event) => {
              event.currentTarget.focus();
              setPanel("conversations");
            }}
          >
            <Icon name="menu" />
          </button>
          <div className="brand-mark">
            <Icon name="shield" size={22} />
          </div>
          <div className="brand-copy">
            <h1>Yagami</h1>
            <p>AI context firewall</p>
          </div>
          <nav className="workspace-actions" aria-label="Workspace tools">
            <button
              className="toolbar-button"
              aria-label="Cross-session memory"
              title="Cross-session memory"
              onClick={(event) => {
                event.currentTarget.focus();
                setPanel("memory");
              }}
            >
              <Icon name="memory" />
              <span className="toolbar-label">Memory</span>
            </button>
            <button
              className="toolbar-button"
              aria-label="Stats dashboard"
              title="Stats dashboard"
              onClick={(event) => {
                event.currentTarget.focus();
                setPanel("stats");
              }}
            >
              <Icon name="activity" />
              <span className="toolbar-label">Activity</span>
            </button>
            <button
              className="toolbar-button"
              aria-label="Settings"
              title="Settings"
              onClick={(event) => {
                event.currentTarget.focus();
                setPanel("settings");
              }}
            >
              <Icon name="settings" />
              <span className="toolbar-label">Settings</span>
            </button>
            <button
              className="toolbar-button compact-insights"
              aria-label="Session insights"
              title="Session insights"
              onClick={(event) => {
                event.currentTarget.focus();
                setPanel("details");
              }}
            >
              <Icon name="shield" />
            </button>
          </nav>
        </header>
        {health?.demo_mode &&
          (health.default_backend === "echo" || health.mode === "echo-demo" ? (
            <div role="status" className="demo-banner">
              <strong>Echo demonstration:</strong> no AI model is running. Start{" "}
              <code>yagami serve</code> for real local model responses.
            </div>
          ) : (
            <div role="status" className="demo-banner local-demo">
              <strong>Local model demonstration:</strong> responses use your
              configured local model. Cloud routing is disabled.
            </div>
          ))}
        <Chat
          key={chatKey}
          loadSessionId={loadSessionId}
          onRouting={setRouting}
          onSession={setSessionId}
          onTurnComplete={() => setRefreshKey((key) => key + 1)}
        />
      </main>
      <aside className="insights-rail" aria-label="Session insights">
        {details}
      </aside>
      {(panel === "conversations" || panel === "details") && (
        <Dialog
          title={
            panel === "conversations" ? "Conversations" : "Session insights"
          }
          onClose={() => setPanel(null)}
          className="compact-panel"
        >
          <div className="dialog-heading">
            <h2>
              {panel === "conversations" ? "Conversations" : "Session insights"}
            </h2>
            <button
              className="toolbar-button"
              aria-label="Close"
              onClick={() => setPanel(null)}
            >
              <Icon name="close" />
            </button>
          </div>
          {panel === "conversations" ? sidebar : details}
        </Dialog>
      )}
      <ToastHost />
      <ShortcutSheet />
      <SettingsModal
        open={panel === "settings"}
        onClose={() => setPanel(null)}
      />
      <StatsDashboard open={panel === "stats"} onClose={() => setPanel(null)} />
      <MemoryPanel open={panel === "memory"} onClose={() => setPanel(null)} />
    </div>
  );
}
