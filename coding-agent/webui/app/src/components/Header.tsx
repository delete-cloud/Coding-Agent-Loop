import { useEffect, useState } from "react";
import type { AgentClient } from "../lib/api";
import type { ApprovalPolicy } from "../lib/types";

interface Config {
  baseUrl: string;
  apiKey: string;
  repoPath: string;
  approval: ApprovalPolicy;
  provider: string;
  model: string;
}

// Mirrors ProviderName in src/coding_agent/server/schemas.py.
const PROVIDERS = [
  "openai",
  "anthropic",
  "copilot",
  "kimi",
  "kimi-code",
  "kimi-code-anthropic",
  "deepseek",
  "stepfun",
  "codex",
] as const;

// Fallback datalist options, used when the server has no live model list.
const MODEL_PRESETS = ["kimi-for-coding", "k3", "deepseek-chat"] as const;

interface Props {
  config: Config;
  onConfigChange: (patch: Partial<Config>) => void;
  onNewSession: () => void;
  onShowDiff: () => void;
  sessionId: string | null;
  status: string;
  client: AgentClient;
  theme: "dark" | "light";
  onToggleTheme: () => void;
}

export default function Header({
  config,
  onConfigChange,
  onNewSession,
  onShowDiff,
  sessionId,
  status,
  client,
  theme,
  onToggleTheme,
}: Props) {
  // Live model ids for the selected provider; null = use MODEL_PRESETS.
  const [liveModels, setLiveModels] = useState<string[] | null>(null);

  useEffect(() => {
    const provider = config.provider.trim();
    if (!provider) {
      setLiveModels(null);
      return;
    }
    const ctrl = new AbortController();
    // Debounce rapid provider switches; the abort drops superseded requests.
    const timer = setTimeout(() => {
      client
        .listProviderModels(provider, ctrl.signal)
        .then((res) => {
          setLiveModels(res.source === "live" && res.models.length > 0 ? res.models : null);
        })
        .catch(() => setLiveModels(null));
    }, 250);
    return () => {
      clearTimeout(timer);
      ctrl.abort();
    };
  }, [client, config.provider]);

  const modelOptions = liveModels ?? MODEL_PRESETS;

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
        value={config.provider}
        title="provider"
        onChange={(e) => onConfigChange({ provider: e.target.value })}
      >
        <option value="">server default</option>
        {PROVIDERS.map((p) => (
          <option key={p} value={p}>
            {p}
          </option>
        ))}
      </select>
      <input
        className="w-44 rounded-lg border border-border bg-surface-0 px-3 py-1.5 text-sm text-fg placeholder:text-muted focus:border-accent focus:outline-none disabled:opacity-40"
        list="model-options"
        placeholder="model (e.g. kimi-for-coding)"
        title="model"
        value={config.model}
        disabled={!config.provider}
        onChange={(e) => onConfigChange({ model: e.target.value })}
      />
      <datalist id="model-options">
        {modelOptions.map((m) => (
          <option key={m} value={m} />
        ))}
      </datalist>
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
      <button
        type="button"
        className="rounded-lg border border-border px-3 py-1.5 text-sm text-fg transition-colors hover:border-border-active"
        onClick={onToggleTheme}
        aria-label="toggle theme"
        title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
      >
        {theme === "dark" ? "☀️" : "🌙"}
      </button>
      <span className="ml-auto text-xs text-muted">
        {sessionId ? status : "no session"}
      </span>
    </header>
  );
}
