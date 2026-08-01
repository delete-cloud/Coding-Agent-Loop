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
export const PROVIDERS = [
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
// Codex uses the Responses API, which has no models endpoint — the model
// input stays manual with these presets as datalist suggestions.
const CODEX_MODEL_PRESETS = ["gpt-5.5", "gpt-5.4"] as const;

const isCodexProvider = (p: string) => p === "codex" || p.startsWith("codex:");

interface Props {
  config: Config;
  onConfigChange: (patch: Partial<Config>) => void;
  onNewSession: () => void;
  onToggleSidebar: () => void;
  sessionId: string | null;
  status: string;
  client: AgentClient;
  theme: "dark" | "light";
  onToggleTheme: () => void;
  showThinking: boolean;
  onToggleThinking: () => void;
}

export default function Header({
  config,
  onConfigChange,
  onNewSession,
  onToggleSidebar,
  sessionId,
  status,
  client,
  theme,
  onToggleTheme,
  showThinking,
  onToggleThinking,
}: Props) {
  // Live model ids for the selected provider; null = use MODEL_PRESETS.
  const [liveModels, setLiveModels] = useState<string[] | null>(null);
  // Connected codex OAuth account keys ("codex", "codex:<label>") appended to
  // the static provider list; empty when the server lacks the endpoints.
  const [oauthProviders, setOauthProviders] = useState<string[]>([]);

  useEffect(() => {
    let alive = true;
    client
      .listOAuthAccounts()
      .then((accounts) => {
        if (alive) setOauthProviders(accounts.map((a) => a.provider));
      })
      .catch(() => {
        if (alive) setOauthProviders([]);
      });
    return () => {
      alive = false;
    };
  }, [client]);

  useEffect(() => {
    const provider = config.provider.trim();
    if (!provider || isCodexProvider(provider)) {
      // Codex has no live models list — keep the static codex presets.
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

  const modelOptions = isCodexProvider(config.provider.trim())
    ? CODEX_MODEL_PRESETS
    : (liveModels ?? MODEL_PRESETS);
  const providerOptions = [...new Set([...PROVIDERS, ...oauthProviders])];

  return (
    <header className="flex min-w-0 flex-wrap items-center gap-2 border-b border-border bg-surface-1 px-3 py-2.5 sm:px-4">
      <button
        type="button"
        className="rounded-lg border border-border px-2.5 py-1.5 text-sm text-fg transition-colors hover:border-border-active"
        onClick={onToggleSidebar}
        aria-label="toggle sidebar"
        title="Toggle sidebar"
      >
        ☰
      </button>
      <input
        className="min-w-0 flex-1 rounded-lg border border-border bg-surface-0 px-3 py-1.5 text-sm text-fg placeholder:text-muted focus:border-accent focus:outline-none sm:w-48 sm:flex-none"
        value={config.baseUrl}
        title="Server base URL"
        onChange={(e) => onConfigChange({ baseUrl: e.target.value })}
      />
      <input
        className="w-full rounded-lg border border-border bg-surface-0 px-3 py-1.5 text-sm text-fg placeholder:text-muted focus:border-accent focus:outline-none sm:w-36"
        type="password"
        placeholder="X-API-Key"
        value={config.apiKey}
        onChange={(e) => onConfigChange({ apiKey: e.target.value })}
      />
      <input
        className="w-full rounded-lg border border-border bg-surface-0 px-3 py-1.5 text-sm text-fg placeholder:text-muted focus:border-accent focus:outline-none sm:w-52"
        placeholder="repo path (optional)"
        value={config.repoPath}
        onChange={(e) => onConfigChange({ repoPath: e.target.value })}
      />
      <div
        className="grid w-full min-w-0 grid-cols-1 gap-1.5 sm:flex sm:w-auto sm:flex-nowrap sm:items-center"
        title="Defaults applied when creating a new session (per-session overrides live in the Settings panel)"
      >
        <span className="text-[10px] font-medium tracking-wide text-muted uppercase">
          new session defaults
        </span>
        <select
          className="w-full min-w-0 rounded-lg border border-border bg-surface-0 px-3 py-1.5 text-sm text-fg focus:border-accent focus:outline-none sm:w-auto"
          value={config.provider}
          title="provider"
          onChange={(e) => onConfigChange({ provider: e.target.value })}
        >
          <option value="">server default</option>
          {providerOptions.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
        <input
          className="w-full min-w-0 rounded-lg border border-border bg-surface-0 px-3 py-1.5 font-mono text-sm text-fg placeholder:text-muted focus:border-accent focus:outline-none disabled:opacity-40 sm:w-44"
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
      </div>
      <button
        className="rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover"
        onClick={onNewSession}
      >
        New session
      </button>
      <button
        type="button"
        className={`rounded-lg border px-3 py-1.5 text-sm transition-colors ${
          showThinking
            ? "border-accent/50 text-accent"
            : "border-border text-muted hover:border-border-active"
        }`}
        onClick={onToggleThinking}
        aria-label="toggle thinking"
        aria-pressed={showThinking}
        title={showThinking ? "Hide thinking blocks" : "Show thinking blocks"}
      >
        💭
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
      <span className="ml-auto w-full max-w-full truncate text-right text-xs text-muted sm:w-auto">
        {sessionId ? status : "no session"}
      </span>
    </header>
  );
}
