import { useEffect, useState } from "react";

const SHORTCUTS: { keys: string; desc: string }[] = [
  { keys: "Enter", desc: "Send message" },
  { keys: "Shift+Enter", desc: "Newline in message" },
  { keys: "Esc", desc: "Cancel in-flight response / close this sheet" },
  { keys: "Ctrl+K", desc: "Focus the input" },
  { keys: "Ctrl+L", desc: "Start a new chat" },
  { keys: "Ctrl+/", desc: "Show this cheat sheet" },
];

const SLASH: { cmd: string; desc: string }[] = [
  { cmd: "/cloud /claude", desc: "Force this turn to Claude" },
  { cmd: "/local /ollama", desc: "Force this turn to local Ollama" },
  { cmd: "/image", desc: "Force this turn to image generation (Stability)" },
  { cmd: "/think", desc: "Send to Claude with high-complexity hint" },
  { cmd: "/code", desc: "Stay local; tag as a code task" },
  { cmd: "/reset", desc: "Send this turn without prior chat context" },
];

export function ShortcutSheet() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key === "/") {
        e.preventDefault();
        setOpen((v) => !v);
      } else if (e.key === "Escape" && open) {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={() => setOpen(false)}
    >
      <div
        className="bg-zinc-900 border border-zinc-700 rounded-lg p-5 max-w-lg w-full mx-4 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-zinc-100">Keyboard shortcuts</h3>
          <button
            onClick={() => setOpen(false)}
            className="text-zinc-400 hover:text-white text-lg leading-none"
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <ul className="text-xs space-y-1.5">
          {SHORTCUTS.map((s) => (
            <li key={s.keys} className="flex justify-between gap-4">
              <span className="text-zinc-300">{s.desc}</span>
              <kbd className="px-1.5 py-0.5 rounded bg-zinc-800 border border-zinc-700 text-zinc-200 font-mono text-[11px] shrink-0">
                {s.keys}
              </kbd>
            </li>
          ))}
        </ul>
        <h4 className="text-xs font-semibold text-zinc-100 mt-4 mb-2">
          Slash commands (start of message)
        </h4>
        <ul className="text-xs space-y-1.5">
          {SLASH.map((s) => (
            <li key={s.cmd} className="flex justify-between gap-4">
              <span className="text-zinc-300">{s.desc}</span>
              <code className="px-1.5 py-0.5 rounded bg-zinc-800 border border-zinc-700 text-zinc-200 font-mono text-[11px] shrink-0">
                {s.cmd}
              </code>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
