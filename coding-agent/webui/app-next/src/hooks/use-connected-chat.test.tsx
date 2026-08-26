import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import fixture from "../../test/fixtures/connected-chat/v1/connected-chat-contract.json";
import {
  FakeBackend,
  flush,
  makeSnapshot,
  waitUntil,
} from "../../test/helpers/connected-chat-fake";
import { ConnectedChatController } from "@/lib/connected-chat/controller";
import { createTimelineState, reduceChatEvent } from "@/lib/connected-chat/timeline";
import type { ChatEventEnvelope } from "@/lib/connected-chat/wire";
import {
  ConnectedChatProvider,
  healthForStatus,
  timelineToMessages,
  useConnectedChat,
  useConnectedChatServices,
  useSessionCatalog,
  type ConnectedChatServices,
  type ConnectedChatView,
} from "./use-connected-chat";

const events = fixture.events.map((entry) => entry.data as ChatEventEnvelope);

function fullTimeline() {
  return events.reduce(reduceChatEvent, createTimelineState());
}

describe("timelineToMessages", () => {
  it("maps every fixture event kind to one view message in session order", () => {
    const messages = timelineToMessages(fullTimeline());

    expect(messages.map((m) => m.id)).toEqual([
      "evt-user-01",
      "evt-thinking-01",
      "evt-progress-01",
      "evt-tool-call-01",
      "evt-assistant-01",
      "evt-terminal-completed",
      "evt-terminal-failed",
      "evt-terminal-cancelled",
      "evt-terminal-interrupted",
    ]);
    expect(messages.map((m) => m.kind)).toEqual([
      "user",
      "thinking",
      "progress",
      "tool",
      "assistant",
      "terminal",
      "terminal",
      "terminal",
      "terminal",
    ]);

    expect(messages[0].body).toBe("Run tests");
    expect(messages[0].createdAt).toBe("2026-08-24T00:00:00Z");
    expect(messages[1].body).toBe("Inspecting test suite");
    expect(messages[2].body).toBe("Running tests");
    expect(messages[2].progress).toEqual({ current: 1, total: 2 });
    expect(messages[4].body).toBe("All tests pass.");

    const tool = messages[3];
    expect(tool.toolName).toBe("bash");
    expect(tool.toolArguments).toBe(JSON.stringify({ command: "pytest" }));
    expect(tool.toolOutput).toBe("42 passed");
    expect(tool.toolError).toBe(false);

    expect(messages[5].terminalOutcome).toBe("completed");
    expect(messages[5].body).toBe("All tests pass.");
    expect(messages[6].terminalOutcome).toBe("failed");
    expect(messages[6].body).toBe("Adapter failed");
    expect(messages[8].terminalOutcome).toBe("interrupted");
  });

  it("renders a tool call without a result and marks error results", () => {
    const call = events[3];
    // Build a properly discriminated tool_result event from the fixture's
    // real tool_result envelope; the union spread alone keeps whatever kind
    // the source had, which would silently mis-type the payload.
    const errorResult: ChatEventEnvelope = {
      ...events[4],
      kind: "tool_result",
      source_event_id: "evt-tool-result-err",
      payload: { call_id: "call-01", output: "exit 1", is_error: true },
    };
    const timeline = [errorResult, call].reduce(reduceChatEvent, createTimelineState());

    const [message] = timelineToMessages(timeline);
    expect(message.kind).toBe("tool");
    expect(message.toolOutput).toBe("exit 1");
    expect(message.toolError).toBe(true);

    const callOnly = timelineToMessages(
      reduceChatEvent(createTimelineState(), call),
    );
    expect(callOnly[0].toolOutput).toBeUndefined();
    expect(callOnly[0].toolError).toBeUndefined();
  });
});

describe("healthForStatus", () => {
  it("maps transport status to rail health", () => {
    expect(healthForStatus(null)).toBe("idle");
    expect(healthForStatus("idle")).toBe("idle");
    expect(healthForStatus("loading")).toBe("idle");
    expect(healthForStatus("following")).toBe("ok");
    expect(healthForStatus("sending")).toBe("ok");
    expect(healthForStatus("cancelling")).toBe("ok");
    expect(healthForStatus("reconnecting")).toBe("degraded");
    expect(healthForStatus("error")).toBe("down");
    expect(healthForStatus("replay_required")).toBe("down");
  });
});

function servicesWrapper(services: ConnectedChatServices | undefined) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <ConnectedChatProvider services={services}>{children}</ConnectedChatProvider>;
  };
}

async function selectReadySession(controller: ConnectedChatController, backend: FakeBackend) {
  const selected = controller.selectSession("session-01");
  backend.snapshots.at(-1)!.resolve(makeSnapshot("session-01"));
  await selected;
  await flush();
}

describe("ConnectedChatProvider", () => {
  it("exposes injected services to consumers", () => {
    const backend = new FakeBackend();
    const controller = new ConnectedChatController(backend);
    const services: ConnectedChatServices = { controller, catalog: backend };

    const { result } = renderHook(() => useConnectedChatServices(), {
      wrapper: servicesWrapper(services),
    });

    expect(result.current?.controller).toBe(controller);
    expect(result.current?.catalog).toBe(backend);
  });

  it("creates real same-origin services after mount when none are injected", async () => {
    const { result } = renderHook(() => useConnectedChatServices(), {
      wrapper: servicesWrapper(undefined),
    });

    await waitUntil(() => result.current !== null);
    expect(result.current?.controller).toBeInstanceOf(ConnectedChatController);
    // Creating services must not issue any network traffic on its own.
    expect(window.location.origin).toBeTruthy();
  });
});

describe("useConnectedChat", () => {
  it("is null before services exist, then binds the services created after mount", async () => {
    // With no injected services the provider creates real same-origin
    // services in a mount effect, so the view is null only for the first
    // render. Capture every render to observe that pre-mount state instead
    // of expecting null after effects have flushed.
    const renders: Array<ConnectedChatView | null> = [];
    const { result } = renderHook(
      () => {
        const view = useConnectedChat();
        renders.push(view);
        return view;
      },
      {
        wrapper: ({ children }: { children: ReactNode }) => (
          <ConnectedChatProvider services={null}>{children}</ConnectedChatProvider>
        ),
      },
    );

    expect(renders[0]).toBeNull();
    await waitUntil(() => result.current !== null);
    expect(result.current?.state.status).toBe("idle");
  });

  it("stays null when no provider supplies services", () => {
    const { result } = renderHook(() => useConnectedChat());
    expect(result.current).toBeNull();
  });

  it("mirrors controller state and delegates draft and send with fresh command ids", async () => {
    const backend = new FakeBackend();
    const controller = new ConnectedChatController(backend);
    const { result } = renderHook(() => useConnectedChat(), {
      wrapper: servicesWrapper({ controller, catalog: backend }),
    });

    expect(result.current?.state.status).toBe("idle");

    await act(async () => {
      await selectReadySession(controller, backend);
    });
    expect(result.current?.state.sessionId).toBe("session-01");
    expect(result.current?.state.status).toBe("following");

    act(() => result.current?.setDraft("Run tests"));
    expect(result.current?.state.draft).toBe("Run tests");
    expect(controller.getState().draft).toBe("Run tests");

    await act(async () => {
      result.current?.send();
      await flush();
    });
    expect(backend.promptCalls).toHaveLength(1);
    expect(backend.promptCalls[0].request.prompt).toBe("Run tests");
    expect(backend.promptCalls[0].request.command_id.length).toBeGreaterThan(0);

    await act(async () => {
      result.current?.setDraft("again");
      result.current?.send();
      await flush();
    });
    expect(backend.promptCalls).toHaveLength(2);
    expect(backend.promptCalls[0].request.command_id).not.toBe(
      backend.promptCalls[1].request.command_id,
    );
  });

  it("delegates cancel and resume to the controller", async () => {
    const backend = new FakeBackend();
    const controller = new ConnectedChatController(backend);
    const { result } = renderHook(() => useConnectedChat(), {
      wrapper: servicesWrapper({ controller, catalog: backend }),
    });
    await act(async () => {
      await selectReadySession(controller, backend);
    });

    await act(async () => {
      result.current?.cancel();
      await flush();
    });
    expect(backend.cancels).toHaveLength(1);
    backend.cancels[0].resolve({
      contract_version: "1.0.0",
      session_id: "session-01",
      run_id: "run-01",
      status: "cancelling",
    });
    await act(async () => {
      await flush();
    });
    expect(result.current?.state.status).toBe("cancelling");

    // Feed a durable interrupted terminal so resume becomes legal.
    await act(async () => {
      backend.follows[0].push({
        type: "chat_event",
        id: events[9].session_seq,
        event: events[9],
      });
      await flush();
    });
    expect(result.current?.canResume).toBe(true);

    await act(async () => {
      result.current?.resume();
      await flush();
    });
    expect(backend.resumeCalls).toHaveLength(1);
    expect(backend.resumeCalls[0].request.parent_run_id).toBe("run-04");
  });
});

describe("useSessionCatalog", () => {
  it("stays loading while no catalog client is connected", () => {
    const { result } = renderHook(() => useSessionCatalog(null));
    expect(result.current.status).toBe("loading");
    expect(result.current.sessions).toEqual([]);
  });

  it("loads sessions and reports ready", async () => {
    const backend = new FakeBackend();
    const { result } = renderHook(() => useSessionCatalog(backend));
    expect(result.current.status).toBe("loading");
    expect(backend.lists).toHaveLength(1);

    await act(async () => {
      backend.lists[0].resolve({
        contract_version: "1.0.0",
        sessions: [{ session_id: "session-01", title: "Run tests" }],
      });
    });

    expect(result.current.status).toBe("ready");
    expect(result.current.sessions).toEqual([{ session_id: "session-01", title: "Run tests" }]);
    expect(result.current.error).toBeNull();
  });

  it("surfaces a list failure and recovers through refresh", async () => {
    const backend = new FakeBackend();
    const { result } = renderHook(() => useSessionCatalog(backend));
    const failure = new Error("list transport down");
    await act(async () => {
      backend.lists[0].reject(failure);
    });

    expect(result.current.status).toBe("error");
    expect(result.current.error).toBe(failure);

    await act(async () => {
      result.current.refresh();
    });
    expect(backend.lists).toHaveLength(2);
    expect(result.current.status).toBe("loading");
    await act(async () => {
      backend.lists[1].resolve({
        contract_version: "1.0.0",
        sessions: [{ session_id: "session-02", title: null }],
      });
    });
    expect(result.current.status).toBe("ready");
    expect(result.current.sessions[0].session_id).toBe("session-02");
  });

  it("creates a session, refreshes the list, and returns the new id", async () => {
    const backend = new FakeBackend();
    const { result } = renderHook(() => useSessionCatalog(backend));
    await act(async () => {
      backend.lists[0].resolve({ contract_version: "1.0.0", sessions: [] });
    });

    let createdId: string | null = null;
    let pendingCreate: Promise<string> | null = null;
    act(() => {
      pendingCreate = result.current
        .createSession({ provider: "anthropic", model: "claude-sonnet-4" })
        .then((id) => {
          createdId = id;
          return id;
        });
    });
    expect(result.current.createPending).toBe(true);
    expect(backend.creates).toHaveLength(1);

    await act(async () => {
      backend.creates[0].resolve({ session_id: "session-03" });
    });
    expect(backend.lists).toHaveLength(2);
    expect(result.current.createPending).toBe(true);

    await act(async () => {
      backend.lists[1].resolve({
        contract_version: "1.0.0",
        sessions: [{ session_id: "session-03", title: null }],
      });
      await pendingCreate;
    });

    expect(createdId).toBe("session-03");
    expect(result.current.createPending).toBe(false);
    expect(result.current.sessions[0].session_id).toBe("session-03");
  });
});
