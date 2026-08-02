import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgentClient, type RuntimeConfigPatch } from "./lib/api";
import {
  activeProfile,
  defaultBaseUrl,
  loadProfiles,
  saveProfiles,
  type ProfileStore,
} from "./lib/profiles";
import type {
  ApprovalPolicy,
  ApprovalScope,
  ContextPackItem,
  DisplayEventEnvelope,
  DisplayStreamEvent,
  FinalResultPayload,
  MemoryReviewRecord,
  ProgressPayload,
  SessionResult,
  SessionSummary,
  WorkspaceDiff,
  WorkspacePatch,
} from "./lib/types";
import {
  applyEvent,
  isRootTurnEnd,
  pushUser,
  replayEvents,
  resolveApproval,
  type TimelineItem,
} from "./lib/timeline";
import Timeline from "./components/Timeline";
import Header from "./components/Header";
import Composer from "./components/Composer";
import SessionList from "./components/SessionList";
import DiffPanel from "./components/DiffPanel";
import MemoryPanel, { extractRecallHits } from "./components/MemoryPanel";
import RightRail, { type RailPanel } from "./components/RightRail";
import SettingsPanel from "./components/SettingsPanel";
import CheckpointsPanel from "./components/CheckpointsPanel";
import ResultPanel, { hasResultContent } from "./components/ResultPanel";

const LS_KEY = "coding-agent-webui-config";
const THEME_LS_KEY = "coding-agent-webui-theme";
const THINKING_LS_KEY = "coding-agent-webui-show-thinking";
const RESULT_RETRY_DELAYS_MS = [0, 250, 500, 1_000, 2_000, 4_000] as const;
type Theme = "dark" | "light";
type Config = {
  baseUrl: string;
  apiKey: string;
  repoPath: string;
  approval: ApprovalPolicy;
  provider: string;
  model: string;
};

function loadConfig(): Config {
  const defaults: Config = {
    baseUrl: defaultBaseUrl(),
    apiKey: "",
    repoPath: "",
    approval: "auto",
    provider: "kimi-code",
    model: "kimi-for-coding",
  };
  let merged = defaults;
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (raw) merged = { ...defaults, ...(JSON.parse(raw) as Partial<Config>) };
  } catch {
    /* ignore */
  }
  // The active connection profile owns baseUrl/apiKey; the legacy config's
  // copies are only migration input for the profiles store.
  const active = activeProfile(loadProfiles());
  if (active) {
    merged.baseUrl = active.baseUrl;
    merged.apiKey = active.apiKey;
  }
  return merged;
}

function loadTheme(): Theme {
  try {
    return localStorage.getItem(THEME_LS_KEY) === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

function loadShowThinking(): boolean {
  try {
    return localStorage.getItem(THINKING_LS_KEY) !== "0";
  } catch {
    return true;
  }
}

function initialSidebarOpen(): boolean {
  return (
    typeof window.matchMedia !== "function" ||
    !window.matchMedia("(max-width: 767px)").matches
  );
}

export default function App() {
  const [config, setConfig] = useState<Config>(loadConfig);
  const [profiles, setProfiles] = useState<ProfileStore>(loadProfiles);
  const [theme, setTheme] = useState<Theme>(loadTheme);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessionMode, setSessionMode] = useState<"prompt" | "resume">("prompt");
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [sessionsError, setSessionsError] = useState<string | null>(null);
  const [items, setItems] = useState<TimelineItem[]>([]);
  const [status, setStatus] = useState("no session");
  const [streaming, setStreaming] = useState(false);
  const [sessionLoading, setSessionLoading] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [diffState, setDiffState] = useState<{
    diff: WorkspaceDiff;
    patch: WorkspacePatch | null;
  } | null>(null);
  const [memoryState, setMemoryState] = useState<{
    hits: ContextPackItem[];
    memories: MemoryReviewRecord[];
    error: string | null;
  } | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(initialSidebarOpen);
  const [railPanel, setRailPanel] = useState<RailPanel>(null);
  const [showThinking, setShowThinking] = useState<boolean>(loadShowThinking);
  const [resultState, setResultState] = useState<SessionResult | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const followAbortRef = useRef<AbortController | null>(null);
  const loadSeqRef = useRef(0);
  const sessionsSeqRef = useRef(0);
  const memorySeqRef = useRef(0);
  const resultSeqRef = useRef(0);
  const diffSeqRef = useRef(0);
  const activeSessionRef = useRef<string | null>(null);
  const sessionLoadingRef = useRef(false);

  const client = useMemo(
    () => new AgentClient({ baseUrl: config.baseUrl, apiKey: config.apiKey }),
    [config.baseUrl, config.apiKey],
  );

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem(THEME_LS_KEY, theme);
    } catch {
      /* ignore */
    }
  }, [theme]);

  const toggleTheme = useCallback(() => {
    setTheme((t) => (t === "dark" ? "light" : "dark"));
  }, []);

  const toggleThinking = useCallback(() => {
    setShowThinking((v) => {
      try {
        localStorage.setItem(THINKING_LS_KEY, v ? "0" : "1");
      } catch {
        /* ignore */
      }
      return !v;
    });
  }, []);

  const patchConfig = useCallback((p: Partial<Config>) => {
    setConfig((c) => {
      const next = { ...c, ...p };
      try {
        localStorage.setItem(LS_KEY, JSON.stringify(next));
      } catch {
        /* ignore */
      }
      return next;
    });
  }, []);

  // Per-session runtime settings (model/provider/thinking/approval). Approval
  // changes also update the persisted new-session default.
  const updateSessionRuntime = useCallback(
    async (id: string, patch: RuntimeConfigPatch) => {
      const updated = await client.updateRuntimeConfig(id, patch);
      setSessions((current) =>
        current.map((entry) => {
          if (entry.session_id !== id) return entry;
          return {
            ...entry,
            provider_name:
              patch.provider !== undefined ? updated.provider_name : entry.provider_name,
            model_name: patch.model !== undefined ? updated.model_name : entry.model_name,
            base_url: patch.baseUrl !== undefined ? updated.base_url : entry.base_url,
          };
        }),
      );
      if (patch.approval !== undefined) patchConfig({ approval: patch.approval });
    },
    [client, patchConfig],
  );

  const setSessionLoadingState = useCallback((value: boolean) => {
    sessionLoadingRef.current = value;
    setSessionLoading(value);
  }, []);

  // Persist profile changes and, when the active connection (baseUrl/apiKey)
  // actually changed, switch backend: drop all session state tied to the old
  // server and push the new connection through the normal config path (which
  // recreates the client and re-fetches the session list).
  const handleProfilesChange = useCallback(
    (next: ProfileStore) => {
      saveProfiles(next);
      setProfiles(next);
      const active = activeProfile(next);
      if (!active) return;
      if (active.baseUrl === config.baseUrl && active.apiKey === config.apiKey) return;
      loadSeqRef.current += 1;
      // Invalidate any in-flight session list fetch from the old backend so its
      // late response cannot overwrite the new backend's list.
      sessionsSeqRef.current += 1;
      abortRef.current?.abort();
      abortRef.current = null;
      followAbortRef.current?.abort();
      followAbortRef.current = null;
      activeSessionRef.current = null;
      setSessionId(null);
      setSessionMode("prompt");
      setItems([]);
      setSessions([]);
      setDiffState(null);
      setMemoryState(null);
      setResultState(null);
      setRailPanel(null);
      setStreaming(false);
      setSessionLoadingState(false);
      setStatus("no session");
      patchConfig({ baseUrl: active.baseUrl, apiKey: active.apiKey });
    },
    [config.baseUrl, config.apiKey, patchConfig, setSessionLoadingState],
  );

  const refreshSessions = useCallback(async () => {
    // Refreshes can resolve out of order (e.g. a slow old backend vs. a fresh
    // fetch after a profile switch); only the latest call may apply. Same
    // seq-guard pattern as refreshMemory/refreshResult.
    const sessionsSeq = ++sessionsSeqRef.current;
    setSessionsLoading(true);
    setSessionsError(null);
    try {
      const listed = await client.listSessions();
      if (sessionsSeqRef.current !== sessionsSeq) return;
      listed.sort(
        (a, b) =>
          new Date(b.last_activity).getTime() - new Date(a.last_activity).getTime(),
      );
      setSessions(listed);
    } catch (e) {
      if (sessionsSeqRef.current !== sessionsSeq) return;
      setSessionsError(`session list failed: ${msg(e)}`);
    } finally {
      if (sessionsSeqRef.current === sessionsSeq) setSessionsLoading(false);
    }
  }, [client]);

  const refreshMemory = useCallback(async (id: string) => {
    // Skip work for sessions that are no longer active; this also keeps stale
    // callbacks from invalidating the sequence of the current session.
    if (activeSessionRef.current !== id) return;
    // Same-session refreshes can resolve out of order; only the latest may apply.
    const memorySeq = ++memorySeqRef.current;
    try {
      const [runs, memories] = await Promise.all([
        client.runs(id),
        client.listMemoryReviews(id, "accepted"),
      ]);
      if (activeSessionRef.current !== id || memorySeqRef.current !== memorySeq) return;
      setMemoryState({ hits: extractRecallHits(runs), memories, error: null });
    } catch (e) {
      if (activeSessionRef.current !== id || memorySeqRef.current !== memorySeq) return;
      setMemoryState((prev) => ({
        hits: prev?.hits ?? [],
        memories: prev?.memories ?? [],
        error: `memory load failed: ${msg(e)}`,
      }));
    }
  }, [client]);

  // Session result (final_answer / verification / failure). Same seq-guard
  // pattern as refreshMemory; failures just keep the previous result hidden.
  const refreshResult = useCallback(async (id: string, retryEmpty = false) => {
    if (activeSessionRef.current !== id) return;
    const resultSeq = ++resultSeqRef.current;
    const delays = retryEmpty ? RESULT_RETRY_DELAYS_MS : RESULT_RETRY_DELAYS_MS.slice(0, 1);
    for (const delay of delays) {
      if (delay > 0) await new Promise((resolve) => setTimeout(resolve, delay));
      if (activeSessionRef.current !== id || resultSeqRef.current !== resultSeq) return;
      try {
        const result = await client.result(id);
        if (activeSessionRef.current !== id || resultSeqRef.current !== resultSeq) return;
        setResultState(result);
        if (hasResultContent(result)) return;
      } catch {
        // A failed refresh keeps the previous result rather than clobbering it.
        return;
      }
    }
  }, [client]);

  useEffect(() => {
    // refreshSessions is keyed on the client, so this also re-fetches after a
    // profile switch or connection edit recreates the client.
    void refreshSessions();
  }, [refreshSessions]);

  const newSession = useCallback(async () => {
    const loadSeq = loadSeqRef.current + 1;
    const previousSessionId = activeSessionRef.current;
    try {
      loadSeqRef.current = loadSeq;
      abortRef.current?.abort();
      abortRef.current = null;
      followAbortRef.current?.abort();
      followAbortRef.current = null;
      activeSessionRef.current = null;
      setSessionId(null);
      setSessionMode("prompt");
      setSessionLoadingState(true);
      setStreaming(false);
      setStatus("creating session…");
      // Provider "server default" (empty) means: let the server pick, send neither field.
      const provider = config.provider.trim();
      const model = provider ? config.model.trim() : "";
      const id = await client.createSession({
        repoPath: config.repoPath,
        approvalPolicy: config.approval,
        provider: provider || undefined,
        model: model || undefined,
      });
      if (loadSeqRef.current !== loadSeq) return;
      activeSessionRef.current = id;
      setSessionId(id);
      setSessionMode("prompt");
      setSessionLoadingState(false);
      setItems([]);
      setDiffState(null);
      setMemoryState(null);
      setResultState(null);
      setRailPanel(null);
      setStatus("idle");
      void refreshSessions();
      void refreshMemory(id);
    } catch (e) {
      if (loadSeqRef.current !== loadSeq) return;
      activeSessionRef.current = previousSessionId;
      setSessionId(previousSessionId);
      setSessionLoadingState(false);
      setStatus(previousSessionId ? "idle" : "no session");
      setItems([
        {
          id: `e${Date.now()}`,
          kind: "error",
          text: `create failed: ${msg(e)}`,
        },
      ]);
    }
  }, [client, config.repoPath, config.approval, config.provider, config.model, refreshSessions, refreshMemory, setSessionLoadingState]);

  const deleteSession = useCallback(async (id: string) => {
    if (!window.confirm(`Close session ${id}? This stops its task and removes it.`)) return;
    try {
      await client.closeSession(id);
      setSessions((prev) => prev.filter((it) => it.session_id !== id));
      if (activeSessionRef.current === id) {
        loadSeqRef.current += 1;
        abortRef.current?.abort();
        abortRef.current = null;
        followAbortRef.current?.abort();
        followAbortRef.current = null;
        activeSessionRef.current = null;
        setSessionId(null);
        setItems([]);
        setDiffState(null);
        setMemoryState(null);
        setResultState(null);
        setRailPanel(null);
        setStreaming(false);
        setSessionLoadingState(false);
        setStatus("no session");
      }
      // Re-fetch after the DELETE resolved so an in-flight list issued earlier
      // cannot be the last word and re-add the deleted session.
      void refreshSessions();
    } catch (e) {
      setSessionsError(`close session failed: ${msg(e)}`);
    }
  }, [client, refreshSessions, setSessionLoadingState]);

  const followSession = useCallback(async (id: string) => {
    const ctrl = new AbortController();
    followAbortRef.current = ctrl;
    try {
      for await (const ev of client.followDisplayEvents(id, ctrl.signal)) {
        if (ctrl.signal.aborted || activeSessionRef.current !== id) break;
        if (ev.event === "progress_update") {
          const d = (ev.data as DisplayEventEnvelope<ProgressPayload>).payload;
          setStatus(formatProgressStatus(d));
        } else {
          setItems((prev) => applyEvent(prev, ev));
        }
        if (isRootTurnEnd(ev)) break;
      }
      if (!ctrl.signal.aborted && activeSessionRef.current === id) {
        await reconcileSession(id, "");
      }
    } catch (e) {
      if (!ctrl.signal.aborted && activeSessionRef.current === id) {
        await reconcileSession(id, `stream interrupted: ${msg(e)}`);
      }
    } finally {
      if (followAbortRef.current === ctrl) followAbortRef.current = null;
      void refreshSessions();
      void refreshMemory(id);
      void refreshResult(id);
    }
  }, [client, refreshSessions, refreshMemory, refreshResult]);

  const loadSession = useCallback(async (id: string) => {
    const loadSeq = loadSeqRef.current + 1;
    loadSeqRef.current = loadSeq;
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
    followAbortRef.current?.abort();
    followAbortRef.current = null;
    activeSessionRef.current = id;
    setSessionId(id);
    setSessionMode("prompt");
    setSessionLoadingState(true);
    setItems([]);
    setDiffState(null);
    setMemoryState(null);
    setResultState(null);
    setRailPanel(null);
    setStatus("loading history…");
    try {
      const [summary, events] = await Promise.all([
        client.getSession(id),
        client.displayEvents(id),
      ]);
      if (loadSeqRef.current !== loadSeq || activeSessionRef.current !== id) return;
      setSessions((prev) => upsertSession(prev, summary));
      setSessionMode(shouldResumeSession(summary) ? "resume" : "prompt");
      setSessionLoadingState(false);
      setItems(replayEvents([], events));
      setStatus(summary.turn_in_progress ? "reconnected" : "idle");
      void refreshMemory(id);
      void refreshResult(id);
      if (summary.turn_in_progress) void followSession(id);
    } catch (e) {
      if (loadSeqRef.current !== loadSeq || activeSessionRef.current !== id) return;
      setSessionLoadingState(false);
      setItems([
        {
          id: `e${Date.now()}`,
          kind: "error",
          text: `restore failed: ${msg(e)}`,
        },
      ]);
      setStatus("restore failed");
      // memoryState was reset to null above; resolve it so the memory panel
      // shows an error (or recovered data) instead of loading forever.
      void refreshMemory(id);
    }
  }, [client, followSession, refreshMemory, refreshResult, setSessionLoadingState]);

  async function reconcileSession(id: string, errorText: string) {
    try {
      const [summary, events] = await Promise.all([
        client.getSession(id),
        client.displayEvents(id),
      ]);
      if (activeSessionRef.current !== id) return;
      setSessions((prev) => upsertSession(prev, summary));
      const replayed = replayEvents([], events);
      setItems(errorText ? [
        ...replayed,
        {
          id: `e${Date.now()}`,
          kind: "error",
          text: errorText,
        },
      ] : replayed);
      setStatus(summary.turn_in_progress ? "reconnected" : "idle");
    } catch (e) {
      if (activeSessionRef.current !== id) return;
      setItems((prev) => [
        ...prev,
        {
          id: `e${Date.now()}`,
          kind: "error",
          text: `${errorText}; reconcile failed: ${msg(e)}`,
        },
      ]);
    }
  }

  const send = useCallback(async () => {
    const text = prompt.trim();
    if (!text || !sessionId || streaming || sessionLoadingRef.current) return;
    setPrompt("");
    setItems((prev) => pushUser(prev, text));
    setStreaming(true);
    setStatus("running…");
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    let reconciled = false;
    let resumeSucceeded = false;
    const usedResume = sessionMode === "resume";
    try {
      const stream =
        usedResume
          ? client.resume(sessionId, text, ctrl.signal)
          : client.prompt(sessionId, text, ctrl.signal);
      for await (const ev of stream) {
        if (ctrl.signal.aborted || activeSessionRef.current !== sessionId) break;
        if (ev.event === "progress_update") {
          const d = (ev.data as DisplayEventEnvelope<ProgressPayload>).payload;
          setStatus(formatProgressStatus(d));
        } else {
          setItems((prev) => applyEvent(prev, ev));
        }
        const rootTurnEnded = isRootTurnEnd(ev);
        if (rootTurnEnded && isCompletedRootTurnEnd(ev)) resumeSucceeded = true;
        if (rootTurnEnded) break;
      }
    } catch (e) {
      if (!ctrl.signal.aborted && activeSessionRef.current === sessionId) {
        await reconcileSession(sessionId, `stream error: ${msg(e)}`);
        reconciled = true;
      }
    } finally {
      if (abortRef.current === ctrl) {
        setStreaming(false);
        if (!reconciled) setStatus("idle");
        if (usedResume && resumeSucceeded) setSessionMode("prompt");
        abortRef.current = null;
      }
      void refreshSessions();
      if (activeSessionRef.current === sessionId) {
        void refreshMemory(sessionId);
        // The stream can close just before terminal result details are durable.
        void refreshResult(sessionId, true);
      }
    }
  }, [client, prompt, sessionId, sessionMode, streaming, refreshSessions, refreshMemory, refreshResult]);

  const onApprove = useCallback(
    async (
      requestId: string,
      approved: boolean,
      feedback: string,
      scope: ApprovalScope,
    ) => {
      if (!sessionId) return;
      const approvalSessionId = sessionId;
      setItems((prev) =>
        resolveApproval(prev, requestId, approved ? "approved" : "denied"),
      );
      try {
        await client.approve(approvalSessionId, requestId, approved, feedback, scope);
      } catch (e) {
        if (activeSessionRef.current !== approvalSessionId) return;
        setItems((prev) => [
          // Roll back the optimistic resolve so the card is actionable again.
          ...prev.map((it) =>
            it.kind === "approval" && it.requestId === requestId
              ? { ...it, resolved: undefined }
              : it,
          ),
          {
            id: `e${Date.now()}`,
            kind: "error",
            text: `approve failed: ${msg(e)}`,
          },
        ]);
      }
    },
    [client, sessionId],
  );

  const showDiff = useCallback(async () => {
    if (!sessionId) return;
    // Skip work for sessions that are no longer active, and let only the
    // latest fetch apply. Same seq-guard pattern as refreshMemory/refreshResult.
    if (activeSessionRef.current !== sessionId) return;
    const diffSeq = ++diffSeqRef.current;
    try {
      const d = await client.diff(sessionId);
      let patch: WorkspacePatch | null = null;
      try {
        patch = await client.patch(sessionId);
      } catch {
        patch = null;
      }
      if (activeSessionRef.current !== sessionId || diffSeqRef.current !== diffSeq) return;
      setDiffState({ diff: d, patch });
    } catch (e) {
      if (activeSessionRef.current !== sessionId || diffSeqRef.current !== diffSeq) return;
      setItems((prev) => [
        ...prev,
        {
          id: `e${Date.now()}`,
          kind: "error",
          text: `diff failed: ${msg(e)}`,
        },
      ]);
    }
  }, [client, sessionId]);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    if (sessionId) void client.cancel(sessionId);
  }, [client, sessionId]);

  const toggleRail = useCallback(
    (panel: Exclude<RailPanel, null>) => {
      if (railPanel === panel) {
        setRailPanel(null);
        return;
      }
      setRailPanel(panel);
      // The diff is fetched on demand when its panel opens; memory data is
      // kept fresh by the normal session lifecycle.
      if (panel === "diff") void showDiff();
    },
    [railPanel, showDiff],
  );

  // After a checkpoint restore, replay the restored session through the same
  // load path used for session select, then reopen the checkpoints panel.
  const reloadAfterRestore = useCallback(async (id: string) => {
    if (activeSessionRef.current !== id) return;
    await loadSession(id);
    // loadSession no-ops when the user switched sessions mid-restore; don't
    // reopen the checkpoints panel for a session that is no longer active.
    if (activeSessionRef.current !== id) return;
    setRailPanel("checkpoints");
  }, [loadSession]);

  const selectSession = useCallback(
    (id: string) => {
      // On small screens the sidebar is an overlay; close it after picking.
      if (
        typeof window.matchMedia === "function" &&
        window.matchMedia("(max-width: 767px)").matches
      ) {
        setSidebarOpen(false);
      }
      void loadSession(id);
    },
    [loadSession],
  );

  const activeSession = useMemo(
    () => sessions.find((s) => s.session_id === sessionId) ?? null,
    [sessions, sessionId],
  );

  return (
    <div className="flex h-screen w-full overflow-hidden">
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/50 md:hidden"
          aria-hidden
          onClick={() => setSidebarOpen(false)}
        />
      )}
      <SessionList
        sessions={sessions}
        activeSessionId={sessionId}
        loading={sessionsLoading}
        error={sessionsError}
        open={sidebarOpen}
        onRefresh={refreshSessions}
        onSelect={selectSession}
        onDelete={deleteSession}
        onNewSession={newSession}
      />
      <div className="mr-11 flex min-w-0 flex-1 flex-col md:mr-0">
        <Header
          config={config}
          onConfigChange={patchConfig}
          profiles={profiles}
          onProfilesChange={handleProfilesChange}
          onNewSession={newSession}
          onToggleSidebar={() => setSidebarOpen((v) => !v)}
          sessionId={sessionId}
          status={status}
          client={client}
          theme={theme}
          onToggleTheme={toggleTheme}
          showThinking={showThinking}
          onToggleThinking={toggleThinking}
        />
        <main className="flex min-h-0 flex-1 flex-col">
          {resultState && hasResultContent(resultState) && (
            <ResultPanel key={sessionId ?? "none"} result={resultState} />
          )}
          <Timeline items={items} onApprove={onApprove} showThinking={showThinking} />
          <Composer
            prompt={prompt}
            onPromptChange={setPrompt}
            onSend={send}
            onCancel={cancel}
            disabled={!sessionId || streaming || sessionLoading}
            streaming={streaming}
          />
        </main>
      </div>
      <RightRail panel={railPanel} onToggle={toggleRail}>
        {railPanel === "diff" ? (
          diffState ? (
            <DiffPanel
              diff={diffState.diff}
              patch={diffState.patch}
              onClose={() => {
                setDiffState(null);
                setRailPanel(null);
              }}
            />
          ) : (
            <RailPlaceholder text={sessionId ? "Loading diff…" : "No active session"} />
          )
        ) : railPanel === "memory" ? (
          sessionId ? (
            <MemoryPanel
              hits={memoryState?.hits ?? []}
              memories={memoryState?.memories ?? []}
              loading={memoryState === null}
              error={memoryState?.error ?? null}
              client={client}
              sessionId={sessionId}
              onReviewsChanged={() => void refreshMemory(sessionId)}
            />
          ) : (
            <RailPlaceholder text="No active session" />
          )
        ) : railPanel === "checkpoints" ? (
          sessionId ? (
            <CheckpointsPanel
              key={sessionId}
              client={client}
              sessionId={sessionId}
              onRestored={() => reloadAfterRestore(sessionId)}
              onCaptured={() => void refreshSessions()}
            />
          ) : (
            <RailPlaceholder text="No active session" />
          )
        ) : railPanel === "settings" ? (
          <SettingsPanel
            key={sessionId ?? "global"}
            sessionId={sessionId}
            providerName={activeSession?.provider_name ?? null}
            modelName={activeSession?.model_name ?? null}
            onUpdate={(patch) =>
              sessionId ? updateSessionRuntime(sessionId, patch) : Promise.resolve()
            }
            client={client}
          />
        ) : null}
      </RightRail>
    </div>
  );
}

function RailPlaceholder({ text }: { text: string }) {
  return (
    <div className="flex h-full items-center justify-center px-4 text-sm text-muted">
      {text}
    </div>
  );
}

function msg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function formatProgressStatus(d: ProgressPayload): string {
  return `${d.phase ?? "running"} · ${d.model_name || "?"} · ↑${d.tokens_in ?? 0} ↓${d.tokens_out ?? 0} · ctx ${Math.round(d.context_percent ?? 0)}% · ${(d.elapsed_seconds ?? 0).toFixed(1)}s`;
}

function shouldResumeSession(session: SessionSummary): boolean {
  return (
    session.resumable &&
    session.last_run_status === "interrupted" &&
    Boolean(session.last_interrupted_run_id || session.resume_from_event_id)
  );
}

function isCompletedRootTurnEnd(ev: DisplayStreamEvent): boolean {
  const envelope = ev.data as DisplayEventEnvelope<FinalResultPayload>;
  return envelope.payload.completion_status === "completed";
}

function upsertSession(
  sessions: SessionSummary[],
  session: SessionSummary,
): SessionSummary[] {
  const next = [session, ...sessions.filter((it) => it.session_id !== session.session_id)];
  next.sort(
    (a, b) =>
      new Date(b.last_activity).getTime() - new Date(a.last_activity).getTime(),
  );
  return next;
}
