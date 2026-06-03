import type { ApprovalPolicy } from "../lib/types";

interface Config {
  baseUrl: string;
  apiKey: string;
  repoPath: string;
  approval: ApprovalPolicy;
}

interface Props {
  config: Config;
  onConfigChange: (patch: Partial<Config>) => void;
  onNewSession: () => void;
  onShowDiff: () => void;
  sessionId: string | null;
  status: string;
}

export default function Header({
  config,
  onConfigChange,
  onNewSession,
  onShowDiff,
  sessionId,
  status,
}: Props) {
  return (
    <header className="flex flex-wrap items-center gap-2 border-b border-border bg-surface-1 px-4 py-2.5">
      <input
        className="w-48 rounded-lg border border-border bg-surface-0 px-3 py-1.5 text-sm text-fg placeholder:text-muted focus:border-accent focus:outline-none"
        value={config.baseUrl}
        title="Server base URL"
        onChange={(e) => onConfigChange({ baseUrl: e.target.value })}
      />
      <input
        className="w-36 rounded-lg border border-border bg-surface-0 px-3 py-1.5 text-sm text-fg placeholder:text-muted focus:border-accent focus:outline-none"
        type="password"
        placeholder="X-API-Key"
        value={config.apiKey}
        onChange={(e) => onConfigChange({ apiKey: e.target.value })}
      />
      <input
        className="w-52 rounded-lg border border-border bg-surface-0 px-3 py-1.5 text-sm text-fg placeholder:text-muted focus:border-accent focus:outline-none"
        placeholder="repo path (optional)"
        value={config.repoPath}
        onChange={(e) => onConfigChange({ repoPath: e.target.value })}
      />
      <select
        className="rounded-lg border border-border bg-surface-0 px-3 py-1.5 text-sm text-fg focus:border-accent focus:outline-none"
        value={config.approval}
        title="approval policy"
        onChange={(e) =>
          onConfigChange({ approval: e.target.value as ApprovalPolicy })
        }
      >
        <option value="auto">auto</option>
        <option value="interactive">interactive</option>
        <option value="yolo">yolo</option>
      </select>
      <button
        className="rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover"
        onClick={onNewSession}
      >
        New session
      </button>
      <button
        className="rounded-lg border border-border px-4 py-1.5 text-sm text-fg transition-colors hover:border-border-active disabled:opacity-40"
        disabled={!sessionId}
        onClick={onShowDiff}
      >
        Diff
      </button>
      <span className="ml-auto text-xs text-muted">
        {sessionId ? status : "no session"}
      </span>
    </header>
  );
}
