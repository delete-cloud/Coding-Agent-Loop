import { useEffect, useState } from "react";
import type { AgentClient } from "../lib/api";
import { activeProfile, type ProfileStore } from "../lib/profiles";
import type { ApprovalPolicy } from "../lib/types";
import ConnectionPanel from "./ConnectionPanel";

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
// Codex fallback when the live listing (server-side /backend-api/codex/models)
// is unavailable.
const CODEX_MODEL_PRESETS = [
  "gpt-5.6-sol",
  "gpt-5.6-terra",
  "gpt-5.6-luna",
  "gpt-5.5",
  "gpt-5.4",
] as const;

const isCodexProvider = (p: string) => p === "codex" || p.startsWith("codex:");

interface Props {
  config: Config;
  onConfigChange: (patch: Partial<Config>) => void;
  profiles: ProfileStore;
  onProfilesChange: (store: ProfileStore) => void;
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
  profiles,
  onProfilesChange,
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
  const [panelOpen, setPanelOpen] = useState(false);
  // Last background health check against the active profile's server.
  const [health, setHealth] = useState<
    | { state: "unknown" }
    | { state: "ok"; version: string }
    | { state: "failed"; error: string }
  >({ state: "unknown" });

  // Debounced background health check; re-runs when the client is recreated
  // (profile switch or connection edit). Stale requests are aborted.
  useEffect(() => {
    const ctrl = new AbortController();
    setHealth({ state: "unknown" });
    const timer = setTimeout(() => {
      client
        .health(ctrl.signal)
        .then((h) => {
          if (!ctrl.signal.aborted) setHealth({ state: "ok", version: h.version });
        })
        .catch((e) => {
          if (ctrl.signal.aborted) return;
          setHealth({
            state: "failed",
            error: e instanceof Error ? e.message : String(e),
          });
        });
    }, 200);
    return () => {
      clearTimeout(timer);
      ctrl.abort();
    };
  }, [client]);

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

  const modelOptions =
    liveModels ??
    (isCodexProvider(config.provider.trim()) ? CODEX_MODEL_PRESETS : MODEL_PRESETS);
  const providerOptions = [...new Set([...PROVIDERS, ...oauthProviders])];

  const active = activeProfile(profiles);
  const dotColor =
    health.state === "ok" ? "bg-ok" : health.state === "failed" ? "bg-err" : "bg-muted";
  const indicatorTitle =
    health.state === "failed"
      ? `connection failed: ${health.error}`
      : (active?.baseUrl ?? config.baseUrl);

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
      <div className="relative" data-connection-root>
        <button
          type="button"
          className="flex items-center gap-2 rounded-lg border border-border px-2.5 py-1.5 text-sm text-fg transition-colors hover:border-border-active"
          onClick={() => setPanelOpen((v) => !v)}
          aria-label="connection"
          aria-expanded={panelOpen}
          title={indicatorTitle}
        >
          <span className={`h-2 w-2 shrink-0 rounded-full ${dotColor}`} />
          <span className="max-w-40 truncate">{active?.name ?? "connection"}</span>
          {health.state === "ok" && health.version && (
            <span className="font-mono text-xs text-muted">v{health.version}</span>
          )}
        </button>
        {panelOpen && (
          <ConnectionPanel
            store={profiles}
            onChange={onProfilesChange}
            onClose={() => setPanelOpen(false)}
          />
        )}
      </div>
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
