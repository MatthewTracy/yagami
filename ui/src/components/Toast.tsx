import { useEffect, useState } from "react";

export type ToastKind = "error" | "warning" | "info";

export type Toast = {
  id: number;
  kind: ToastKind;
  text: string;
};

let _nextId = 1;

export function emitToast(kind: ToastKind, text: string) {
  window.dispatchEvent(
    new CustomEvent("yagami:toast", {
      detail: { id: _nextId++, kind, text },
    }),
  );
}

export function ToastHost() {
  const [items, setItems] = useState<Toast[]>([]);

  useEffect(() => {
    const onToast = (e: Event) => {
      const t = (e as CustomEvent<Toast>).detail;
      setItems((cur) => [...cur, t]);
      setTimeout(() => {
        setItems((cur) => cur.filter((x) => x.id !== t.id));
      }, 5000);
    };
    window.addEventListener("yagami:toast", onToast);
    return () => window.removeEventListener("yagami:toast", onToast);
  }, []);

  if (items.length === 0) return null;

  return (
    <div className="fixed top-4 right-4 z-50 flex flex-col gap-2 max-w-md pointer-events-none">
      {items.map((t) => (
        <div
          key={t.id}
          className={`pointer-events-auto px-3 py-2 rounded shadow-lg text-sm border ${
            t.kind === "error"
              ? "bg-red-950/95 border-red-800 text-red-100"
              : t.kind === "warning"
                ? "bg-amber-950/95 border-amber-800 text-amber-100"
                : "bg-zinc-900/95 border-zinc-700 text-zinc-200"
          }`}
          role={t.kind === "error" ? "alert" : "status"}
        >
          <div className="flex items-start gap-2">
            <span className="text-base leading-none mt-0.5">
              {t.kind === "error" ? "⚠" : t.kind === "warning" ? "⚠" : "ℹ"}
            </span>
            <div className="flex-1 break-words">{t.text}</div>
            <button
              onClick={() => setItems((cur) => cur.filter((x) => x.id !== t.id))}
              className="text-zinc-400 hover:text-white text-base leading-none ml-1"
              aria-label="Dismiss"
            >
              ×
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
