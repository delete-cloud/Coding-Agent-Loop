"use client";

// Thin React adapter over the framework-independent connected-chat core.
//
// The provider owns the real ConnectedChatClient (same-origin by default;
// NEXT_PUBLIC_CODING_AGENT_API_URL is the only override) and the
// ConnectedChatController built from it. Tests inject both through the
// `services` prop, so no fetch ever runs in jsdom unless a fake allows it.
//
// Views receive plain presentational values only: no transport types, no
// cursors, no envelopes. Timeline projection and rail-health mapping are pure
// functions exported for direct unit tests.

import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";

import {
  ConnectedChatClient,
  resolveApiBase,
} from "@/lib/connected-chat/client";
import {
  ConnectedChatController,
  type ConnectedChatState,
  type ConnectedChatStatus,
} from "@/lib/connected-chat/controller";
import type { TimelineState } from "@/lib/connected-chat/timeline";
import type { CreateSessionRequest } from "@/lib/connected-chat/client";
import type {
  ChatSessionList,
  ChatSessionSummary,
  SessionCreated,
  TerminalOutcome,
} from "@/lib/connected-chat/wire";

// ---------------------------------------------------------------------------
// View models (presentational only)
// ---------------------------------------------------------------------------

export type TimelineMessageKind =
  | "user"
  | "assistant"
  | "thinking"
  | "progress"
  | "tool"
  | "terminal";

export interface TimelineMessage {
  /** Stable logical identity: the primary event's source_event_id. */
  id: string;
  kind: TimelineMessageKind;
  body: string;
  /** RFC3339 creation time of the primary event; components format it. */
  createdAt: string;
  toolName?: string;
  toolArguments?: string;
  toolOutput?: string;
  toolError?: boolean;
  progress?: { current: number; total: number };
  terminalOutcome?: TerminalOutcome;
}

/** Project the canonical timeline into view messages. Pure and total. */
export function timelineToMessages(timeline: TimelineState): TimelineMessage[] {
  return timeline.order.map((id) => {
    const node = timeline.byId.get(id);
    if (!node) throw new Error(`timeline order references a missing node: ${id}`);
    const { event, result } = node;
    const base = { id: event.source_event_id, createdAt: event.created_at };
    switch (event.kind) {
      case "user_prompt":
        return { ...base, kind: "user", body: event.payload.text };
      case "assistant_message":
        return { ...base, kind: "assistant", body: event.payload.text };
      case "thinking":
        return { ...base, kind: "thinking", body: event.payload.text };
      case "progress":
        return {
          ...base,
          kind: "progress",
          body: event.payload.label,
          progress: { current: event.payload.current, total: event.payload.total },
        };
      case "tool_call": {
        const message: TimelineMessage = {
          ...base,
          kind: "tool",
          body: event.payload.tool_name,
          toolName: event.payload.tool_name,
          toolArguments: JSON.stringify(event.payload.arguments),
        };
        if (result !== null) {
          if (result.kind !== "tool_result") {
            throw new Error(`tool_call ${event.payload.call_id} merged a non-result event`);
          }
          message.toolOutput = result.payload.output;
          message.toolError = result.payload.is_error;
        }
        return message;
      }
      case "root_terminal":
        return {
          ...base,
          kind: "terminal",
          body: event.payload.error?.message ?? event.payload.result ?? "",
          terminalOutcome: event.payload.outcome,
        };
      case "tool_result":
        // Unreachable: the reducer merges results into their call node and
        // never lists them in order. Fail loudly if that invariant breaks.
        throw new Error(`orphan tool_result reached the view: ${event.source_event_id}`);
    }
  });
}

export type RailHealth = "idle" | "ok" | "degraded" | "down";

/** Transport health for the rail dot. Durable truth stays in the timeline. */
export function healthForStatus(status: ConnectedChatStatus | null): RailHealth {
  switch (status) {
    case "following":
    case "sending":
    case "cancelling":
      return "ok";
    case "reconnecting":
      return "degraded";
    case "error":
    case "replay_required":
      return "down";
    default:
      return "idle";
  }
}

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export interface SessionCatalogClient {
  listSessions(signal?: AbortSignal): Promise<ChatSessionList>;
  createSession(request: CreateSessionRequest, signal?: AbortSignal): Promise<SessionCreated>;
  closeSession(sessionId: string, signal?: AbortSignal): Promise<void>;
}

export interface ConnectedChatServices {
  controller: ConnectedChatController;
  catalog: SessionCatalogClient;
}

const ConnectedChatContext = createContext<ConnectedChatServices | null>(null);

export function ConnectedChatProvider({
  services,
  children,
}: {
  /** Test seam. When omitted, real same-origin services are created on mount. */
  services?: ConnectedChatServices | null;
  children: ReactNode;
}) {
  const [created, setCreated] = useState<ConnectedChatServices | null>(services ?? null);

  useEffect(() => {
    // Injected services are owned by the caller; never dispose them here.
    if (services !== undefined && services !== null) return;
    // Browser-only creation: the static prerender has no window, and the
    // contract forbids an implicit localhost guess — the page's own origin
    // is the default, NEXT_PUBLIC_CODING_AGENT_API_URL the only override.
    const client = new ConnectedChatClient({
      baseUrl: resolveApiBase(
        { NEXT_PUBLIC_CODING_AGENT_API_URL: process.env.NEXT_PUBLIC_CODING_AGENT_API_URL },
        window.location.origin,
      ),
    });
    const controller = new ConnectedChatController(client);
    setCreated({ controller, catalog: client });
    return () => controller.dispose();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return createElement(ConnectedChatContext.Provider, { value: created }, children);
}

export function useConnectedChatServices(): ConnectedChatServices | null {
  return useContext(ConnectedChatContext);
}

// ---------------------------------------------------------------------------
// Controller view hook
// ---------------------------------------------------------------------------

export interface ConnectedChatView {
  state: ConnectedChatState;
  messages: TimelineMessage[];
  canResume: boolean;
  /** True while an owning prompt/resume stream is active. */
  busy: boolean;
  setDraft(draft: string): void;
  send(): void;
  cancel(): void;
  resume(): void;
  /** Canonical reload of the selected session (replay-required recovery). */
  reload(): void;
}

function newCommandId(): string {
  return crypto.randomUUID();
}

export function useConnectedChat(): ConnectedChatView | null {
  const services = useContext(ConnectedChatContext);
  const controller = services?.controller ?? null;

  const subscribe = useCallback(
    (listener: () => void) => (controller ? controller.subscribe(listener) : () => {}),
    [controller],
  );
  const getSnapshot = useCallback(
    () => controller?.getState() ?? null,
    [controller],
  );
  const state = useSyncExternalStore(subscribe, getSnapshot, () => null);

  return useMemo(() => {
    if (!controller || state === null) return null;
    const terminal = state.durableTerminal;
    const sending = state.status === "sending";
    let newerRun = false;
    if (terminal !== null) {
      let sawTerminal = false;
      for (const id of state.timeline.order) {
        const event = state.timeline.byId.get(id)?.event;
        if (event === undefined) continue;
        if (event.kind === "root_terminal" && event.run_id === terminal.runId) {
          sawTerminal = true;
          continue;
        }
        if (sawTerminal && event.run_id !== null && event.run_id !== terminal.runId) {
          newerRun = true;
          break;
        }
      }
    }
    const canResume =
      terminal !== null && terminal.outcome !== "completed" && !sending && !newerRun;
    return {
      state,
      messages: timelineToMessages(state.timeline),
      canResume,
      busy: state.status === "sending" || state.status === "cancelling",
      setDraft: (draft: string) => controller.setDraft(draft),
      send: () => {
        const current = controller.getState();
        if (current.status === "replay_required") return;
        const prompt = current.draft;
        if (prompt.trim().length === 0) return;
        void controller.send(prompt, newCommandId());
      },
      cancel: () => {
        void controller.cancel();
      },
      resume: () => {
        if (controller.getState().status === "replay_required") return;
        void controller.resume(newCommandId());
      },
      reload: () => {
        const sessionId = controller.getState().sessionId;
        if (sessionId === null) throw new Error("cannot reload without a selected session");
        void controller.selectSession(sessionId);
      },
    };
  }, [controller, state]);
}

// ---------------------------------------------------------------------------
// Session catalog hook (list/create)
// ---------------------------------------------------------------------------

export interface SessionCatalogView {
  status: "loading" | "ready" | "error";
  sessions: ChatSessionSummary[];
  error: unknown | null;
  createPending: boolean;
  refresh(): void;
  /** Creates a session and awaits the refreshed list; returns the new id. */
  createSession(request: CreateSessionRequest): Promise<string>;
}

export function useSessionCatalog(client: SessionCatalogClient | null): SessionCatalogView {
  const [state, setState] = useState<{
    status: "loading" | "ready" | "error";
    sessions: ChatSessionSummary[];
    error: unknown | null;
  }>({ status: "loading", sessions: [], error: null });
  const [createPending, setCreatePending] = useState(false);
  const generationRef = useRef(0);

  const load = useCallback(async (): Promise<void> => {
    if (!client) {
      setState({ status: "loading", sessions: [], error: null });
      return;
    }
    const generation = ++generationRef.current;
    setState((previous) => ({ ...previous, status: "loading", error: null }));
    try {
      const list = await client.listSessions();
      if (generation !== generationRef.current) return;
      setState({ status: "ready", sessions: list.sessions, error: null });
    } catch (error) {
      if (generation !== generationRef.current) return;
      setState((previous) => ({ status: "error", sessions: previous.sessions, error }));
    }
  }, [client]);

  useEffect(() => {
    void load();
  }, [load]);

  const refresh = useCallback(() => {
    void load();
  }, [load]);

  const createSession = useCallback(async (request: CreateSessionRequest): Promise<string> => {
    if (!client) throw new Error("cannot create a session before the catalog is connected");
    setCreatePending(true);
    try {
      const created = await client.createSession(request);
      await load();
      return created.session_id;
    } finally {
      setCreatePending(false);
    }
  }, [client, load]);

  return { ...state, createPending, refresh, createSession };
}
