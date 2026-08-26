export const SETTINGS_LS_KEY = "night-console-session-defaults";

export const BUILTIN_PROVIDERS = [
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

export type BuiltinProvider = (typeof BUILTIN_PROVIDERS)[number];



export interface SessionDefaults {
  provider: string;
  model: string;
  base_url: string;
}

export const DEFAULT_SESSION_DEFAULTS: SessionDefaults = {
  provider: "anthropic",
  model: "claude-sonnet-4",
  base_url: "",
};

export function isCodexProvider(provider: string): boolean {
  return provider === "codex" || provider.startsWith("codex:");
}

/** Bare `codex` is not a connected account when only `codex:<label>` exists. */
export function resolveProviderAccount(provider: string, oauthProviders: readonly string[]): string {
  if (provider !== "codex") return provider;
  if (oauthProviders.includes("codex")) return "codex";
  const labeled = oauthProviders.filter((item) => item.startsWith("codex:"));
  if (labeled.length === 1) return labeled[0];
  if (labeled.length > 1) return labeled[0];
  return provider;
}

export function listableProviders(oauthProviders: readonly string[]): string[] {
  const builtins = BUILTIN_PROVIDERS.filter((item) => item !== "codex" || oauthProviders.includes("codex"));
  return [...new Set([...builtins, ...oauthProviders])];
}

export function formatProviderAccountLabel(
  provider: string,
  accounts: readonly { provider: string; label: string }[],
): string {
  if (!isCodexProvider(provider)) return provider;
  const account = accounts.find((item) => item.provider === provider);
  const label = account?.label.trim() ?? "";
  if (label.length > 0 && label !== provider) return `Codex · ${label}`;
  return "Codex";
}

export function errorMessageOf(error: unknown): string {
  if (typeof error === "object" && error !== null) {
    if (
      "error" in error &&
      typeof error.error === "object" &&
      error.error !== null &&
      "message" in error.error &&
      typeof error.error.message === "string" &&
      error.error.message.trim()
    ) {
      return error.error.message.trim();
    }
    if ("message" in error && typeof error.message === "string" && error.message.trim()) {
      return error.message.trim();
    }
  }
  if (typeof error === "string" && error.trim()) return error.trim();
  return "";
}

export function isTapeReboundError(error: unknown): boolean {
  return /tape target cannot be rebound/i.test(errorMessageOf(error));
}

export function formatProviderModel(provider: string, model: string): string {
  const nextProvider = provider.trim();
  const nextModel = model.trim();
  if (nextProvider.length > 0 && nextModel.length > 0) {
    return `${nextProvider} · ${nextModel}`;
  }
  return nextProvider || nextModel;
}

export function loadSessionDefaults(): SessionDefaults {
  try {
    const raw = localStorage.getItem(SETTINGS_LS_KEY);
    if (!raw) return { ...DEFAULT_SESSION_DEFAULTS };
    const parsed = JSON.parse(raw) as Partial<SessionDefaults>;
    return {
      provider:
        typeof parsed.provider === "string" && parsed.provider.trim()
          ? parsed.provider.trim()
          : DEFAULT_SESSION_DEFAULTS.provider,
      model:
        typeof parsed.model === "string" && parsed.model.trim()
          ? parsed.model.trim()
          : DEFAULT_SESSION_DEFAULTS.model,
      base_url: typeof parsed.base_url === "string" ? parsed.base_url.trim() : "",
    };
  } catch {
    return { ...DEFAULT_SESSION_DEFAULTS };
  }
}

export function persistSessionDefaults(defaults: SessionDefaults): void {
  const payload: SessionDefaults = {
    provider: defaults.provider.trim(),
    model: defaults.model.trim(),
    base_url: defaults.base_url.trim(),
  };
  try {
    localStorage.setItem(SETTINGS_LS_KEY, JSON.stringify(payload));
  } catch {
    /* ignore quota / private mode */
  }
}


