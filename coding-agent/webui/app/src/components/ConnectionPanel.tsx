import { useEffect, useRef, useState } from "react";
import { AgentClient } from "../lib/api";
import {
  addProfile,
  removeProfile,
  setActiveProfile,
  updateProfile,
  type ConnectionProfile,
  type ProfileStore,
} from "../lib/profiles";

interface Props {
  store: ProfileStore;
  onChange: (store: ProfileStore) => void;
  onClose: () => void;
}

type Mode = { kind: "list" } | { kind: "new" } | { kind: "edit"; id: string };

type TestState =
  | { state: "idle" }
  | { state: "testing" }
  | { state: "ok"; message: string }
  | { state: "failed"; message: string };

const inputCls =
  "w-full rounded-lg border border-border bg-surface-0 px-3 py-1.5 text-sm text-fg placeholder:text-muted focus:border-accent focus:outline-none";

function hostOf(baseUrl: string): string {
  try {
    return new URL(baseUrl).host || baseUrl;
  } catch {
    return baseUrl;
  }
}

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

// Dropdown panel for managing connection profiles. Rendered by the header
// inside a `relative` wrapper marked with `data-connection-root`; clicks
// inside that root (including the toggle button) are not "outside" clicks.
export default function ConnectionPanel({ store, onChange, onClose }: Props) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [mode, setMode] = useState<Mode>({ kind: "list" });
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [test, setTest] = useState<TestState>({ state: "idle" });
  const [saveAnyway, setSaveAnyway] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Refs mirror the current form values so a settling test promise can tell
  // whether the values it validated are still what the form shows.
  const baseUrlRef = useRef(baseUrl);
  baseUrlRef.current = baseUrl;
  const apiKeyRef = useRef(apiKey);
  apiKeyRef.current = apiKey;

  useEffect(() => {
    const onPointerDown = (e: MouseEvent) => {
      const target = e.target;
      if (!(target instanceof Element)) return;
      if (target.closest("[data-connection-root]")) return;
      onClose();
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [onClose]);

  const openForm = (next: Mode) => {
    setMode(next);
    setError(null);
    setTest({ state: "idle" });
    setSaveAnyway(false);
    if (next.kind === "edit") {
      const p = store.profiles.find((it) => it.id === next.id);
      setName(p?.name ?? "");
      setBaseUrl(p?.baseUrl ?? "");
      setApiKey(p?.apiKey ?? "");
    } else {
      setName("");
      setBaseUrl("");
      setApiKey("");
    }
  };

  // Any form edit invalidates the previous test result.
  const onFieldChange = (setter: (v: string) => void) => (v: string) => {
    setter(v);
    setTest({ state: "idle" });
    setSaveAnyway(false);
  };

  // Snapshot of the values the in-flight test is validating. A late response
  // only applies when the form still holds these exact values.
  type TestSnapshot = { baseUrl: string; apiKey: string };
  const testSnapshotMatches = (snapshot: TestSnapshot) =>
    baseUrlRef.current.trim() === snapshot.baseUrl && apiKeyRef.current === snapshot.apiKey;

  const testConnection = async () => {
    setTest({ state: "testing" });
    const started = performance.now();
    const snapshot: TestSnapshot = { baseUrl: baseUrl.trim(), apiKey };
    const client = new AgentClient({ baseUrl: snapshot.baseUrl, apiKey: snapshot.apiKey });
    try {
      const h = await client.health();
      // /healthz is unauthenticated, so also probe an authenticated endpoint
      // with the form's key; a wrong key must not unlock Save.
      try {
        await client.listSessions();
      } catch (e) {
        const message = errMsg(e);
        if (/^40[13]\b/.test(message)) {
          throw new Error(`API key rejected (${message})`);
        }
        throw e;
      }
      if (!testSnapshotMatches(snapshot)) return;
      const latency = Math.round(performance.now() - started);
      setTest({
        state: "ok",
        message: `v${h.version} · ${h.sessions} sessions · ${latency}ms`,
      });
    } catch (e) {
      if (!testSnapshotMatches(snapshot)) return;
      setTest({ state: "failed", message: errMsg(e) });
    }
  };

  const canSave =
    name.trim().length > 0 &&
    baseUrl.trim().length > 0 &&
    (test.state === "ok" || (test.state === "failed" && saveAnyway));

  const save = () => {
    if (!canSave) return;
    const input = { name: name.trim(), baseUrl: baseUrl.trim(), apiKey };
    try {
      if (mode.kind === "edit") {
        onChange(updateProfile(store, mode.id, input));
      } else {
        onChange(addProfile(store, input));
      }
      setMode({ kind: "list" });
    } catch (e) {
      setError(errMsg(e));
    }
  };

  const switchTo = (id: string) => {
    onChange(setActiveProfile(store, id));
    onClose();
  };

  const remove = (p: ConnectionProfile) => {
    if (!window.confirm(`Delete profile "${p.name}" (${hostOf(p.baseUrl)})?`)) return;
    try {
      onChange(removeProfile(store, p.id));
    } catch (e) {
      setError(errMsg(e));
    }
  };

  const formOpen = mode.kind !== "list";

  return (
    <div
      ref={rootRef}
      role="dialog"
      aria-label="connection profiles"
      className="absolute right-0 z-50 mt-2 w-80 rounded-xl border border-border bg-surface-1 p-3 shadow-lg"
    >
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[10px] font-medium tracking-wide text-muted uppercase">
          connection profiles
        </span>
        {!formOpen && (
          <button
            type="button"
            className="rounded-lg border border-border px-2 py-0.5 text-xs text-fg transition-colors hover:border-border-active"
            onClick={() => openForm({ kind: "new" })}
          >
            + New
          </button>
        )}
      </div>

      {error && <div className="mb-2 text-xs text-err">{error}</div>}

      {!formOpen && (
        <ul className="flex max-h-64 flex-col gap-1 overflow-y-auto">
          {store.profiles.map((p) => {
            const isActive = p.id === store.activeId;
            const deletable = !isActive && store.profiles.length > 1;
            return (
              <li
                key={p.id}
                className={`flex items-center gap-2 rounded-lg border px-2 py-1.5 ${
                  isActive ? "border-accent/50 bg-surface-2" : "border-border"
                }`}
              >
                <span
                  className={`h-2 w-2 shrink-0 rounded-full ${isActive ? "bg-ok" : "bg-muted"}`}
                  title={isActive ? "active" : "inactive"}
                />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm text-fg">{p.name}</div>
                  <div className="truncate font-mono text-[11px] text-muted">
                    {hostOf(p.baseUrl)}
                  </div>
                </div>
                {!isActive && (
                  <button
                    type="button"
                    className="rounded border border-border px-1.5 py-0.5 text-[11px] text-fg transition-colors hover:border-border-active"
                    onClick={() => switchTo(p.id)}
                  >
                    Switch
                  </button>
                )}
                <button
                  type="button"
                  className="rounded border border-border px-1.5 py-0.5 text-[11px] text-fg transition-colors hover:border-border-active"
                  onClick={() => openForm({ kind: "edit", id: p.id })}
                >
                  Edit
                </button>
                <button
                  type="button"
                  className="rounded border border-border px-1.5 py-0.5 text-[11px] text-err transition-colors hover:border-border-active disabled:cursor-not-allowed disabled:opacity-40"
                  disabled={!deletable}
                  title={
                    deletable
                      ? `Delete ${p.name}`
                      : isActive
                        ? "Cannot delete the active profile"
                        : "Cannot delete the last profile"
                  }
                  onClick={() => remove(p)}
                >
                  Delete
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {formOpen && (
        <div className="flex flex-col gap-2">
          <input
            className={inputCls}
            placeholder="profile name"
            title="profile name"
            value={name}
            onChange={(e) => onFieldChange(setName)(e.target.value)}
          />
          <input
            className={`${inputCls} font-mono`}
            placeholder="http://localhost:8080"
            title="Server base URL"
            value={baseUrl}
            onChange={(e) => onFieldChange(setBaseUrl)(e.target.value)}
          />
          <input
            className={inputCls}
            type="password"
            placeholder="X-API-Key"
            value={apiKey}
            onChange={(e) => onFieldChange(setApiKey)(e.target.value)}
          />

          {test.state === "ok" && (
            <div className="text-xs text-ok" role="status">
              ✓ {test.message}
            </div>
          )}
          {test.state === "failed" && (
            <div className="text-xs text-err" role="alert">
              ✗ {test.message}
            </div>
          )}
          {test.state === "failed" && (
            <label className="flex items-center gap-1.5 text-xs text-muted">
              <input
                type="checkbox"
                checked={saveAnyway}
                onChange={(e) => setSaveAnyway(e.target.checked)}
              />
              save anyway (connection test failed)
            </label>
          )}

          <div className="flex items-center gap-2">
            <button
              type="button"
              className="rounded-lg border border-border px-3 py-1.5 text-xs text-fg transition-colors hover:border-border-active disabled:cursor-not-allowed disabled:opacity-40"
              disabled={!baseUrl.trim() || test.state === "testing"}
              onClick={() => void testConnection()}
            >
              {test.state === "testing" ? "Testing…" : "Test connection"}
            </button>
            <button
              type="button"
              className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-40"
              disabled={!canSave}
              title={
                canSave ? "Save profile" : "Run a successful connection test to enable saving"
              }
              onClick={save}
            >
              Save
            </button>
            <button
              type="button"
              className="ml-auto rounded-lg border border-border px-3 py-1.5 text-xs text-muted transition-colors hover:border-border-active"
              onClick={() => setMode({ kind: "list" })}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
