import { useCallback, useMemo, useRef, useState } from "react";
import { AgentClient } from "./lib/api";
import type {
  ApprovalPolicy,
  DisplayEventEnvelope,
  ProgressPayload,
} from "./lib/types";
import {
  applyEvent,
  isRootTurnEnd,
  pushUser,
  resolveApproval,
  type TimelineItem,
} from "./lib/timeline";
import Timeline from "./components/Timeline";
import Header from "./components/Header";
import Composer from "./components/Composer";

const LS_KEY = "coding-agent-webui-config";
type Config = {
  baseUrl: string;
  apiKey: string;
  repoPath: string;
  approval: ApprovalPolicy;
};

function loadConfig(): Config {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (raw) return JSON.parse(raw) as Config;
  } catch {
    /* ignore */
  }
  return {
    baseUrl: "http://127.0.0.1:8080",
    apiKey: "",
    repoPath: "",
    approval: "auto",
  };
}

export default function App() {
  const [config, setConfig] = useState<Config>(loadConfig);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [items, setItems] = useState<TimelineItem[]>([]);
  const [status, setStatus] = useState("no session");
  const [streaming, setStreaming] = useState(false);
  const [prompt, setPrompt] = useState("");
  const abortRef = useRef<AbortController | null>(null);

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

  const newSession = useCallback(async () => {
    try {
      const id = await client.createSession({
        repoPath: config.repoPath,
        approvalPolicy: config.approval,
      });
      setSessionId(id);
      setItems([]);
      setStatus("idle");
    } catch (e) {
      setItems([
        {
          id: `e${Date.now()}`,
          kind: "error",
          text: `create failed: ${msg(e)}`,
        },
      ]);
    }
  }, [client, config.repoPath, config.approval]);

  const send = useCallback(async () => {
    const text = prompt.trim();
    if (!text || !sessionId || streaming) return;
    setPrompt("");
    setItems((prev) => pushUser(prev, text));
    setStreaming(true);
    setStatus("running…");
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      for await (const ev of client.prompt(sessionId, text, ctrl.signal)) {
        if (ev.event === "progress_update") {
          const d = (ev.data as DisplayEventEnvelope<ProgressPayload>).payload;
          setStatus(
            `${d.phase ?? "running"} · ${d.model_name || "?"} · ↑${d.tokens_in ?? 0} ↓${d.tokens_out ?? 0} · ctx ${Math.round(d.context_percent ?? 0)}% · ${(d.elapsed_seconds ?? 0).toFixed(1)}s`,
          );
        } else {
          setItems((prev) => applyEvent(prev, ev));
        }
        if (isRootTurnEnd(ev)) break;
      }
    } catch (e) {
      if (!ctrl.signal.aborted)
        setItems((prev) => [
          ...prev,
          {
            id: `e${Date.now()}`,
            kind: "error",
            text: `stream error: ${msg(e)}`,
          },
        ]);
    } finally {
      setStreaming(false);
      setStatus("idle");
      abortRef.current = null;
    }
  }, [client, prompt, sessionId, streaming]);

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
      const rows =
        d.files
          .map(
            (f) =>
              `${f.status.padEnd(9)} +${f.additions ?? 0} -${f.deletions ?? 0}  ${f.path}`,
          )
          .join("\n") || "(no changes)";
      setItems((prev) => [
        ...prev,
        {
          id: `d${Date.now()}`,
          kind: "tool",
          agentId: "",
          callId: `diff-${Date.now()}`,
          toolName: `diff · +${d.additions} -${d.deletions}`,
          args: {},
          result: rows,
        },
      ]);
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
      <Timeline items={items} onApprove={onApprove} />
      <Composer
        prompt={prompt}
        onPromptChange={setPrompt}
        onSend={send}
        onCancel={cancel}
        disabled={!sessionId || streaming}
        streaming={streaming}
      />
    </div>
  );
}

function msg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
