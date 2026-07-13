import { useEffect, useState } from "react";
import { emitToast } from "./Toast";

type Section = "models" | "routing" | "profiles" | "prompts";

type ProfileOverrides = {
  daily_spend_cap_usd?: number;
  default_backend?: string;
  long_message_token_threshold?: number;
};

type Cfg = {
  config: {
    ollama: { url: string; model: string; classifier_model: string };
    anthropic: { model: string; max_tokens: number };
    stability: { model: string };
    routing: {
      long_message_token_threshold: number;
      phi_must_be_local: boolean;
      default_backend: string;
      lora_variants: Record<string, string>;
      daily_spend_cap_usd: number;
      active_profile: string;
    };
    profiles: Record<string, ProfileOverrides>;
  };
  defaults: Cfg["config"];
  prompts: { phi_medical_default: string };
  notes: { phi_must_be_local: string; live_reload: string };
};

type Props = {
  open: boolean;
  onClose: () => void;
};

export function SettingsModal({ open, onClose }: Props) {
  const [data, setData] = useState<Cfg | null>(null);
  const [tab, setTab] = useState<Section>("models");
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [newProfileName, setNewProfileName] = useState("");

  useEffect(() => {
    if (!open) return;
    fetch("/api/config")
      .then((r) => r.json())
      .then((d: Cfg) => {
        setData(d);
        setDirty(false);
      })
      .catch(() => emitToast("error", "Failed to load /api/config"));
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  if (!data) {
    return (
      <Backdrop onClose={onClose}>
        <div className="text-zinc-300 text-sm">Loading settings…</div>
      </Backdrop>
    );
  }

  function update<K extends keyof Cfg["config"]>(
    section: K,
    patch: Partial<Cfg["config"][K]>,
  ) {
    setData((d) =>
      d ? { ...d, config: { ...d.config, [section]: { ...d.config[section], ...patch } } } : d,
    );
    setDirty(true);
  }

  function updateProfile(name: string, patch: Partial<ProfileOverrides>) {
    setData((d) =>
      d
        ? {
            ...d,
            config: {
              ...d.config,
              profiles: { ...d.config.profiles, [name]: { ...d.config.profiles[name], ...patch } },
            },
          }
        : d,
    );
    setDirty(true);
  }

  function addProfile() {
    const name = newProfileName.trim();
    if (!data || !name || data.config.profiles[name]) return;
    setData((d) =>
      d ? { ...d, config: { ...d.config, profiles: { ...d.config.profiles, [name]: {} } } } : d,
    );
    setNewProfileName("");
    setDirty(true);
  }

  function removeProfile(name: string) {
    setData((d) => {
      if (!d) return d;
      const profiles = { ...d.config.profiles };
      delete profiles[name];
      const active_profile =
        d.config.routing.active_profile === name ? "" : d.config.routing.active_profile;
      return {
        ...d,
        config: { ...d.config, profiles, routing: { ...d.config.routing, active_profile } },
      };
    });
    setDirty(true);
  }

  async function save() {
    if (!data) return;
    setSaving(true);
    try {
      const r = await fetch("/api/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ollama: data.config.ollama,
          anthropic: data.config.anthropic,
          stability: data.config.stability,
          routing: data.config.routing,
          profiles: data.config.profiles,
        }),
      });
      if (!r.ok) {
        emitToast("error", `Save failed (${r.status}): ${await r.text()}`);
        return;
      }
      const next = (await r.json()) as Cfg;
      setData(next);
      setDirty(false);
      emitToast("info", "Settings saved. Routing changes apply next turn.");
    } finally {
      setSaving(false);
    }
  }

  const c = data.config;
  return (
    <Backdrop onClose={onClose}>
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-zinc-100">Settings</h3>
        <button
          onClick={onClose}
          className="text-zinc-400 hover:text-white text-lg leading-none"
          aria-label="Close"
        >
          ×
        </button>
      </div>
      <div className="flex gap-1 mb-3 border-b border-zinc-800">
        {(["models", "routing", "profiles", "prompts"] as Section[]).map((s) => (
          <button
            key={s}
            onClick={() => setTab(s)}
            className={`px-3 py-1.5 text-xs capitalize -mb-px border-b-2 ${
              tab === s
                ? "border-zinc-300 text-zinc-100"
                : "border-transparent text-zinc-500 hover:text-zinc-300"
            }`}
          >
            {s}
          </button>
        ))}
      </div>

      <div className="space-y-3 text-xs">
        {tab === "models" && (
          <>
            <Group title="Ollama (local)">
              <Field
                label="URL"
                value={c.ollama.url}
                onChange={(v) => update("ollama", { url: v })}
              />
              <Field
                label="Generation model"
                value={c.ollama.model}
                onChange={(v) => update("ollama", { model: v })}
              />
              <Field
                label="Classifier model"
                value={c.ollama.classifier_model}
                onChange={(v) => update("ollama", { classifier_model: v })}
              />
            </Group>
            <Group title="Anthropic (Claude)">
              <Field
                label="Model"
                value={c.anthropic.model}
                onChange={(v) => update("anthropic", { model: v })}
              />
              <NumField
                label="Max tokens"
                value={c.anthropic.max_tokens}
                onChange={(v) => update("anthropic", { max_tokens: v })}
              />
            </Group>
            <Group title="Stability (image)">
              <Field
                label="Model"
                value={c.stability.model}
                onChange={(v) => update("stability", { model: v })}
              />
            </Group>
            <p className="text-[10px] text-zinc-500 italic mt-2">
              Note: model URL or name changes need a uvicorn restart to fully take effect.
            </p>
          </>
        )}

        {tab === "routing" && (
          <>
            <Group title="Default routing">
              <SelectField
                label="Default backend"
                value={c.routing.default_backend}
                options={[
                  "ollama",
                  "anthropic",
                  "openai",
                  "mistral",
                  "groq",
                  "openrouter",
                  "gemini",
                  "stability",
                  "echo",
                ]}
                onChange={(v) => update("routing", { default_backend: v })}
              />
              <NumField
                label="Long-message threshold (tokens)"
                value={c.routing.long_message_token_threshold}
                onChange={(v) =>
                  update("routing", { long_message_token_threshold: v })
                }
              />
            </Group>
            <Group title="Spend cap">
              <NumField
                label="Daily cap (USD, 0 = no cap)"
                value={c.routing.daily_spend_cap_usd}
                step={0.5}
                onChange={(v) => update("routing", { daily_spend_cap_usd: v })}
              />
              <p className="text-[10px] text-zinc-500">
                Once today's spend reaches the cap, cloud backends are
                refused with an explicit error. Local Ollama stays available.
              </p>
            </Group>
            <Group title="Privacy (locked)">
              <div className="flex items-center justify-between gap-2">
                <span className="text-zinc-300">PHI must stay local</span>
                <span className="px-1.5 py-0.5 rounded bg-emerald-800 text-emerald-100 font-medium">
                  ON · locked
                </span>
              </div>
              <p className="text-[10px] text-zinc-500">{data.notes.phi_must_be_local}</p>
            </Group>
          </>
        )}

        {tab === "profiles" && (
          <>
            <Group title="Active profile">
              <SelectField
                label="Profile"
                value={c.routing.active_profile}
                options={["", ...Object.keys(c.profiles)]}
                onChange={(v) => update("routing", { active_profile: v })}
              />
              <p className="text-[10px] text-zinc-500">
                "" = no profile - [routing] above applies directly. A
                profile overrides default backend / spend cap / long-message
                threshold only. PHI must stay local either way; no profile
                can change that.
              </p>
            </Group>
            {Object.keys(c.profiles).length === 0 && (
              <p className="text-[10px] text-zinc-500 italic">No profiles yet.</p>
            )}
            {Object.entries(c.profiles).map(([name, p]) => (
              <Group key={name} title={name}>
                <div className="flex items-center justify-between">
                  {name === c.routing.active_profile ? (
                    <span className="px-1.5 py-0.5 rounded bg-emerald-800 text-emerald-100 text-[10px] font-medium">
                      ACTIVE
                    </span>
                  ) : (
                    <span />
                  )}
                  <button
                    onClick={() => removeProfile(name)}
                    className="text-[10px] text-red-400 hover:text-red-300"
                  >
                    Delete profile
                  </button>
                </div>
                <SelectField
                  label="Default backend"
                  value={p.default_backend ?? c.routing.default_backend}
                  options={[
                  "ollama",
                  "anthropic",
                  "openai",
                  "mistral",
                  "groq",
                  "openrouter",
                  "gemini",
                  "stability",
                  "echo",
                ]}
                  onChange={(v) => updateProfile(name, { default_backend: v })}
                />
                <NumField
                  label="Daily cap (USD, 0 = no cap)"
                  value={p.daily_spend_cap_usd ?? c.routing.daily_spend_cap_usd}
                  step={0.5}
                  onChange={(v) => updateProfile(name, { daily_spend_cap_usd: v })}
                />
                <NumField
                  label="Long-message threshold"
                  value={p.long_message_token_threshold ?? c.routing.long_message_token_threshold}
                  onChange={(v) => updateProfile(name, { long_message_token_threshold: v })}
                />
              </Group>
            ))}
            <Group title="New profile">
              <label className="flex items-center gap-2">
                <span className="text-zinc-400 w-44 shrink-0">Name</span>
                <input
                  type="text"
                  value={newProfileName}
                  onChange={(e) => setNewProfileName(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && addProfile()}
                  placeholder="e.g. work"
                  className="flex-1 min-w-0 bg-zinc-950 border border-zinc-800 rounded px-2 py-1 text-zinc-200 font-mono text-[11px]"
                />
                <button
                  onClick={addProfile}
                  disabled={!newProfileName.trim()}
                  className="px-2 py-1 text-[11px] rounded bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40 disabled:cursor-not-allowed text-zinc-200"
                >
                  Add
                </button>
              </label>
            </Group>
          </>
        )}

        {tab === "prompts" && (
          <>
            <Group title="PHI / clinical system prompt">
              <p className="text-[10px] text-zinc-500">
                Sent to local Ollama whenever a turn is classified as
                <code className="ml-1 mr-1 px-1 bg-zinc-800">phi_medical</code>.
                Read-only here - edit{" "}
                <code className="px-1 bg-zinc-800">src/yagami/router/prompts.py</code>{" "}
                to change.
              </p>
              <textarea
                readOnly
                value={data.prompts.phi_medical_default}
                rows={10}
                className="w-full bg-zinc-950 border border-zinc-800 rounded p-2 text-[11px] text-zinc-300 font-mono"
              />
            </Group>
          </>
        )}
      </div>

      <div className="flex justify-between items-center mt-4 pt-3 border-t border-zinc-800">
        <span className="text-[10px] text-zinc-500 italic">{data.notes.live_reload}</span>
        <div className="flex gap-2">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-xs text-zinc-300 hover:text-white"
          >
            Close
          </button>
          <button
            onClick={save}
            disabled={!dirty || saving}
            className="px-3 py-1.5 text-xs rounded bg-emerald-700 hover:bg-emerald-600 disabled:opacity-40 disabled:cursor-not-allowed text-white"
          >
            {saving ? "Saving…" : dirty ? "Save changes" : "Saved"}
          </button>
        </div>
      </div>
    </Backdrop>
  );
}

function Backdrop({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 overflow-y-auto py-8"
      onClick={onClose}
    >
      <div
        className="bg-zinc-900 border border-zinc-700 rounded-lg p-5 max-w-xl w-full mx-4 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </div>
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5 p-2.5 rounded border border-zinc-800 bg-zinc-950/30">
      <div className="text-[10px] uppercase tracking-wider text-zinc-500">{title}</div>
      {children}
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <label className="flex items-center gap-2">
      <span className="text-zinc-400 w-44 shrink-0">{label}</span>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="flex-1 min-w-0 bg-zinc-950 border border-zinc-800 rounded px-2 py-1 text-zinc-200 font-mono text-[11px]"
      />
    </label>
  );
}

function NumField({
  label,
  value,
  step,
  onChange,
}: {
  label: string;
  value: number;
  step?: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className="flex items-center gap-2">
      <span className="text-zinc-400 w-44 shrink-0">{label}</span>
      <input
        type="number"
        value={value}
        step={step ?? 1}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-32 bg-zinc-950 border border-zinc-800 rounded px-2 py-1 text-zinc-200 font-mono text-[11px]"
      />
    </label>
  );
}

function SelectField({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
}) {
  return (
    <label className="flex items-center gap-2">
      <span className="text-zinc-400 w-44 shrink-0">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="flex-1 bg-zinc-950 border border-zinc-800 rounded px-2 py-1 text-zinc-200 font-mono text-[11px]"
      >
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </label>
  );
}
