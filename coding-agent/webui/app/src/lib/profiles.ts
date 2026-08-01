// Connection profiles: named (baseUrl, apiKey) pairs persisted to
// localStorage, with one active profile driving the app's AgentClient.

export interface ConnectionProfile {
  id: string;
  name: string;
  baseUrl: string;
  apiKey: string;
}

export interface ProfileStore {
  profiles: ConnectionProfile[];
  activeId: string;
}

export const PROFILES_LS_KEY = "coding-agent-webui-profiles";
// Pre-profiles config key; its baseUrl/apiKey seed the first profile.
export const LEGACY_CONFIG_LS_KEY = "coding-agent-webui-config";

type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;

function defaultStorage(): StorageLike | null {
  try {
    return typeof localStorage !== "undefined" ? localStorage : null;
  } catch {
    return null;
  }
}

export function defaultBaseUrl(): string {
  const configured = import.meta.env.VITE_API_BASE_URL;
  if (typeof configured === "string" && configured.trim()) {
    return configured.trim();
  }
  return typeof window !== "undefined" ? window.location.origin : "";
}

function generateId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `p-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

// Human-friendly default name derived from the URL host ("localhost:8080").
export function deriveName(baseUrl: string): string {
  try {
    const host = new URL(baseUrl).host;
    if (host) return host;
  } catch {
    /* fall through */
  }
  return "default";
}

export function activeProfile(store: ProfileStore): ConnectionProfile | null {
  return store.profiles.find((p) => p.id === store.activeId) ?? null;
}

function sanitizeProfile(raw: unknown): ConnectionProfile | null {
  const p = (raw ?? {}) as Record<string, unknown>;
  if (typeof p.baseUrl !== "string" || !p.baseUrl.trim()) return null;
  return {
    id: typeof p.id === "string" && p.id ? p.id : generateId(),
    name: typeof p.name === "string" && p.name.trim() ? p.name : deriveName(p.baseUrl),
    baseUrl: p.baseUrl,
    apiKey: typeof p.apiKey === "string" ? p.apiKey : "",
  };
}

// Seed the initial store: migrate the legacy single-config baseUrl/apiKey
// when present, otherwise a same-origin profile.
function seedStore(storage: StorageLike | null): ProfileStore {
  let baseUrl = "";
  let apiKey = "";
  try {
    const raw = storage?.getItem(LEGACY_CONFIG_LS_KEY);
    if (raw) {
      const legacy = JSON.parse(raw) as Record<string, unknown>;
      if (typeof legacy.baseUrl === "string") baseUrl = legacy.baseUrl;
      if (typeof legacy.apiKey === "string") apiKey = legacy.apiKey;
    }
  } catch {
    /* fall through to the same-origin default */
  }
  if (!baseUrl.trim()) {
    baseUrl = defaultBaseUrl();
    apiKey = "";
  }
  const profile: ConnectionProfile = {
    id: generateId(),
    name: deriveName(baseUrl),
    baseUrl,
    apiKey,
  };
  return { profiles: [profile], activeId: profile.id };
}

export function loadProfiles(storage: StorageLike | null = defaultStorage()): ProfileStore {
  try {
    const raw = storage?.getItem(PROFILES_LS_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as { profiles?: unknown; activeId?: unknown };
      const profiles = Array.isArray(parsed.profiles)
        ? parsed.profiles.flatMap((p) => {
            const profile = sanitizeProfile(p);
            return profile ? [profile] : [];
          })
        : [];
      if (profiles.length > 0) {
        const activeId =
          typeof parsed.activeId === "string" &&
          profiles.some((p) => p.id === parsed.activeId)
            ? parsed.activeId
            : profiles[0].id;
        return { profiles, activeId };
      }
    }
  } catch {
    /* corrupt payload — reseed below */
  }
  const seeded = seedStore(storage);
  saveProfiles(seeded, storage);
  return seeded;
}

export function saveProfiles(
  store: ProfileStore,
  storage: StorageLike | null = defaultStorage(),
): void {
  try {
    storage?.setItem(PROFILES_LS_KEY, JSON.stringify(store));
  } catch {
    /* ignore */
  }
}

export function addProfile(
  store: ProfileStore,
  input: { name: string; baseUrl: string; apiKey: string },
): ProfileStore {
  const profile: ConnectionProfile = {
    id: generateId(),
    name: input.name.trim() || deriveName(input.baseUrl),
    baseUrl: input.baseUrl.trim(),
    apiKey: input.apiKey,
  };
  return { ...store, profiles: [...store.profiles, profile] };
}

export function updateProfile(
  store: ProfileStore,
  id: string,
  patch: Partial<{ name: string; baseUrl: string; apiKey: string }>,
): ProfileStore {
  return {
    ...store,
    profiles: store.profiles.map((p) => (p.id === id ? { ...p, ...patch } : p)),
  };
}

// Deleting the active or the last profile is rejected — the app always needs
// one active connection.
export function removeProfile(store: ProfileStore, id: string): ProfileStore {
  if (!store.profiles.some((p) => p.id === id)) return store;
  if (store.profiles.length <= 1) {
    throw new Error("cannot delete the last profile");
  }
  if (store.activeId === id) {
    throw new Error("cannot delete the active profile");
  }
  return { ...store, profiles: store.profiles.filter((p) => p.id !== id) };
}

export function setActiveProfile(store: ProfileStore, id: string): ProfileStore {
  if (!store.profiles.some((p) => p.id === id)) return store;
  return { ...store, activeId: id };
}
