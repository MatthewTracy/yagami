type Props = {
  backend?: string;
  isLocal?: boolean;
  reason?: string;
  classification?: Record<string, unknown>;
};

export function DebugPanel({ backend, isLocal, reason, classification }: Props) {
  if (!backend) {
    return (
      <div className="text-xs text-zinc-500 p-3 border border-zinc-800 rounded-md">
        No routing decision yet - send a message.
      </div>
    );
  }
  const localBadge = isLocal ? (
    <span className="px-1.5 py-0.5 rounded bg-emerald-900 text-emerald-200 text-[10px] font-medium">LOCAL</span>
  ) : (
    <span className="px-1.5 py-0.5 rounded bg-amber-900 text-amber-200 text-[10px] font-medium">CLOUD</span>
  );
  return (
    <div className="text-xs p-3 border border-zinc-800 rounded-md space-y-1.5 bg-zinc-900/50">
      <div className="flex items-center gap-2">
        <span className="font-mono text-zinc-300">{backend}</span>
        {localBadge}
      </div>
      <div className="text-zinc-400">{reason}</div>
      {classification && (
        <pre className="text-[10px] text-zinc-500 overflow-x-auto">
          {JSON.stringify(classification, null, 2)}
        </pre>
      )}
    </div>
  );
}
