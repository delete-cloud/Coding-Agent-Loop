import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgentClient } from "./lib/api";
import type {
  ApprovalPolicy,
  DisplayEventEnvelope,
  DisplayStreamEvent,
  FinalResultPayload,
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

const LS_KEY = "coding-agent-webui-config";
type Config = {
  baseUrl: string;
  apiKey: string;
  repoPath: string;
  approval: ApprovalPolicy;
};

function defaultBaseUrl(): string {
  const configured = import.meta.env.VITE_API_BASE_URL;
  if (typeof configured === "string" && configured.trim()) {
    return configured.trim();
  }
  return window.location.origin;
}

function loadConfig(): Config {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (raw) return JSON.parse(raw) as Config;
  } catch {
    /* ignore */
  }
  return {
    baseUrl: defaultBaseUrl(),
    apiKey: "",
    repoPath: "",
    approval: "auto",
  };
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
  const abortRef = useRef<AbortController | null>(null);
  const followAbortRef = useRef<AbortController | null>(null);
  const loadSeqRef = useRef(0);
  const activeSessionRef = useRef<string | null>(null);
  const sessionLoadingRef = useRef(false);

  const client = useMemo(
    () => new AgentClient({ baseUrl: config.baseUrl, apiKey: config.apiKey }),
    [config.baseUrl, config.apiKey],
  );

  const patchConfig = (p: Partial<Config>) =>
    setConfig((c) => {
      const next = { ...c, ...p };
      try {
        localStorage.setItem(LS_KEY, JSON.stringify(next));
      } catch {
        /* ignore */
      }
      return next;
    });

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
      const id = await client.createSession({
        repoPath: config.repoPath,
        approvalPolicy: config.approval,
      });
      if (loadSeqRef.current !== loadSeq) return;
      activeSessionRef.current = id;
      setSessionId(id);
      setSessionMode("prompt");
      setSessionLoadingState(false);
      setItems([]);
      setDiffState(null);
      setStatus("idle");
      void refreshSessions();
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
  }, [client, config.repoPath, config.approval, refreshSessions, setSessionLoadingState]);

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
    }
  }, [client, refreshSessions]);

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
    }
  }, [client, followSession, setSessionLoadingState]);

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
    }
  }, [client, prompt, sessionId, sessionMode, streaming, refreshSessions]);

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
