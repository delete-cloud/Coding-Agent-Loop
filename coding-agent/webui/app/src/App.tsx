import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgentClient } from "./lib/api";
import type {
  ApprovalPolicy,
  ContextPackItem,
  DisplayEventEnvelope,
  DisplayStreamEvent,
  FinalResultPayload,
  MemoryReviewRecord,
  ProgressPayload,
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

const LS_KEY = "coding-agent-webui-config";
type Config = {
  baseUrl: string;
  apiKey: string;
  repoPath: string;
  approval: ApprovalPolicy;
  provider: string;
  model: string;
};

function defaultBaseUrl(): string {
  const configured = import.meta.env.VITE_API_BASE_URL;
  if (typeof configured === "string" && configured.trim()) {
    return configured.trim();
  }
  return window.location.origin;
}

function loadConfig(): Config {
  const defaults: Config = {
    baseUrl: defaultBaseUrl(),
    apiKey: "",
    repoPath: "",
    approval: "auto",
    provider: "kimi-code",
    model: "kimi-for-coding",
  };
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (raw) return { ...defaults, ...(JSON.parse(raw) as Partial<Config>) };
  } catch {
    /* ignore */
  }
  return defaults;
}

export default function App() {
  const [config, setConfig] = useState<Config>(loadConfig);
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
  const abortRef = useRef<AbortController | null>(null);
  const followAbortRef = useRef<AbortController | null>(null);
  const loadSeqRef = useRef(0);
  const memorySeqRef = useRef(0);
  const activeSessionRef = useRef<string | null>(null);
  const sessionLoadingRef = useRef(false);

  const client = useMemo(
    () => new AgentClient({ baseUrl: config.baseUrl, apiKey: config.apiKey }),
    [config.baseUrl, config.apiKey],
  );

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
    const activeSessionId = activeSessionRef.current;
    if (p.approval !== undefined && activeSessionId) {
      void client.updateRuntimeConfig(activeSessionId, { approval: p.approval }).catch((e) => {
        setItems((prev) => [
          ...prev,
          {
            id: `e${Date.now()}`,
            kind: "error",
            text: `approval policy update failed: ${msg(e)}`,
          },
        ]);
      });
    }
  }, [client]);

  const setSessionLoadingState = useCallback((value: boolean) => {
    sessionLoadingRef.current = value;
    setSessionLoading(value);
  }, []);

  const refreshSessions = useCallback(async () => {
    setSessionsLoading(true);
    setSessionsError(null);
    try {
      const listed = await client.listSessions();
      listed.sort(
        (a, b) =>
          new Date(b.last_activity).getTime() - new Date(a.last_activity).getTime(),
      );
      setSessions(listed);
    } catch (e) {
      setSessionsError(`session list failed: ${msg(e)}`);
    } finally {
      setSessionsLoading(false);
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

  useEffect(() => {
    void refreshSessions();
    // Config edits can pass through incomplete URLs; refresh explicitly after edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
    }
  }, [client, refreshSessions, refreshMemory]);

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
  }, [client, followSession, refreshMemory, setSessionLoadingState]);

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
      if (activeSessionRef.current === sessionId) void refreshMemory(sessionId);
    }
  }, [client, prompt, sessionId, sessionMode, streaming, refreshSessions, refreshMemory]);

  const onApprove = useCallback(
    async (requestId: string, approved: boolean, feedback: string) => {
      if (!sessionId) return;
      setItems((prev) =>
        resolveApproval(prev, requestId, approved ? "approved" : "denied"),
      );
      try {
        await client.approve(sessionId, requestId, approved, feedback);
      } catch (e) {
        setItems((prev) => [
          ...prev,
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
    try {
      const d = await client.diff(sessionId);
      let patch: WorkspacePatch | null = null;
      try {
        patch = await client.patch(sessionId);
      } catch {
        patch = null;
      }
      setDiffState({ diff: d, patch });
    } catch (e) {
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

  return (
    <div className="flex h-screen flex-col">
      <Header
        config={config}
        onConfigChange={patchConfig}
        onNewSession={newSession}
        onShowDiff={showDiff}
        sessionId={sessionId}
        status={status}
      />
      <div className="min-h-0 flex flex-1 flex-col md:flex-row">
        <SessionList
          sessions={sessions}
          activeSessionId={sessionId}
          loading={sessionsLoading}
          error={sessionsError}
          onRefresh={refreshSessions}
          onSelect={loadSession}
          onDelete={deleteSession}
        />
        <main className="flex min-w-0 flex-1 flex-col">
          <Timeline items={items} onApprove={onApprove} />
          {diffState && (
            <DiffPanel
              diff={diffState.diff}
              patch={diffState.patch}
              onClose={() => setDiffState(null)}
            />
          )}
          {sessionId && (
            <MemoryPanel
              hits={memoryState?.hits ?? []}
              memories={memoryState?.memories ?? []}
              loading={memoryState === null}
              error={memoryState?.error ?? null}
            />
          )}
        </main>
      </div>
      <Composer
        prompt={prompt}
        onPromptChange={setPrompt}
        onSend={send}
        onCancel={cancel}
        disabled={!sessionId || streaming || sessionLoading}
        streaming={streaming}
      />
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
