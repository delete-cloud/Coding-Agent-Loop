import { describe, expect, it, vi } from "vitest";

import fixture from "../../../test/fixtures/connected-chat/v1/connected-chat-contract.json";
import {
  FakeBackend,
  chatItem,
  flush,
  makeSnapshot,
  waitUntil,
} from "../../../test/helpers/connected-chat-fake";
import { ChatApiError, ConnectedChatClient, resolveApiBase } from "./client";
import { ConnectedChatController } from "./controller";
import { createTimelineState, reduceChatEvent } from "./timeline";
import {
  ContractViolationError,
  parseCancelAck,
  parseChatEvent,
  parseStreamControl,
  type ChatEventEnvelope,
} from "./wire";

const SESSION_ID = "session-01";
const FOLLOW_CURSOR = fixture.http.follow.cursor;
const events = fixture.events.map((entry) => entry.data as ChatEventEnvelope);

function fixtureEvent(sourceEventId: string): ChatEventEnvelope {
  const event = events.find((candidate) => candidate.source_event_id === sourceEventId);
  if (!event) throw new Error(`fixture is missing ${sourceEventId}`);
  return event;
}

function derivedEvent(
  base: ChatEventEnvelope,
  overrides: Partial<ChatEventEnvelope>,
): ChatEventEnvelope {
  return parseChatEvent({ ...base, ...overrides });
}

async function selectReady(
  controller: ConnectedChatController,
  backend: FakeBackend,
  sessionId = SESSION_ID,
) {
  const selecting = controller.selectSession(sessionId);
  backend.snapshots.at(-1)?.resolve(makeSnapshot(sessionId, [], FOLLOW_CURSOR));
  await selecting;
  await waitUntil(() => backend.follows.length > 0);
}

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

async function drainStream(iter: AsyncGenerator<unknown>): Promise<void> {
  for await (const item of iter) {
    void item;
  }
}

const AUTH_ERROR = fixture.http.errors.auth[0];
const CREDENTIALS_REQUIRED = {
  code: "credentials_required",
  message: "Authentication credentials are required",
  retryable: false,
} as const;

const J8_AUTH_OPERATIONS: ReadonlyArray<{
  name: string;
  run: (client: ConnectedChatClient) => Promise<unknown>;
}> = [
  { name: "listSessions", run: (client) => client.listSessions() },
  { name: "createSession", run: (client) => client.createSession({ provider: "anthropic", model: "claude-sonnet-4" }) },
  { name: "snapshot", run: (client) => client.snapshot(SESSION_ID) },
  {
    name: "follow",
    run: (client) => drainStream(client.follow(SESSION_ID, FOLLOW_CURSOR)),
  },
  {
    name: "prompt",
    run: (client) => drainStream(client.prompt(SESSION_ID, fixture.http.prompt.request)),
  },
  {
    name: "resume",
    run: (client) => drainStream(client.resume(SESSION_ID, fixture.http.resume.request)),
  },
  { name: "cancel", run: (client) => client.cancel(SESSION_ID) },
];

describe("connected-chat fixture journeys J1-J8", () => {
  it("J1: prompt preserves ordered events and exactly one completed root terminal", async () => {
    const backend = new FakeBackend();
    const controller = new ConnectedChatController(backend);
    await selectReady(controller, backend);

    const user = fixtureEvent("evt-user-01");
    const assistant = fixtureEvent("evt-assistant-01");
    const completed = fixtureEvent("evt-terminal-completed");
    const sending = controller.send("Run tests", "cmd-01");

    backend.prompts[0].push(chatItem(user));
    backend.prompts[0].push(chatItem(assistant));
    backend.prompts[0].push(chatItem(completed));
    backend.prompts[0].end();
    await waitUntil(() => backend.snapshots.length === 2);
    backend.snapshots[1].resolve(
      makeSnapshot(SESSION_ID, [user, assistant, completed], FOLLOW_CURSOR),
    );
    await sending;

    const state = controller.getState();
    expect(state.timeline.order).toEqual([
      "evt-user-01",
      "evt-assistant-01",
      "evt-terminal-completed",
    ]);
    expect(
      state.timeline.order.filter(
        (id) => state.timeline.byId.get(id)?.event.kind === "root_terminal",
      ),
    ).toEqual(["evt-terminal-completed"]);
    expect(state.durableTerminal).toEqual({
      outcome: "completed",
      result: "All tests pass.",
      error: null,
      runId: "run-01",
    });
    controller.dispose();
  });

  it("J2: duplicate and out-of-order tool results merge into the prior call", () => {
    const toolCall = fixtureEvent("evt-tool-call-01");
    const toolResult = fixtureEvent("evt-tool-result-01");
    const replay = fixture.overlap_example.replay_source_event_ids.map(fixtureEvent);
    const queued = fixture.overlap_example.queued_source_event_ids.map(fixtureEvent);

    let state = reduceChatEvent(createTimelineState(), toolResult);
    expect(state.pendingToolResults.get("call-01")?.source_event_id).toBe(
      "evt-tool-result-01",
    );

    state = [toolCall, toolCall, toolResult, ...replay, ...queued].reduce(
      reduceChatEvent,
      state,
    );
    expect(state.order).toEqual([
      "evt-tool-call-01",
      "evt-assistant-01",
      "evt-terminal-completed",
    ]);
    expect(state.byId.get("evt-tool-call-01")?.result?.source_event_id).toBe(
      "evt-tool-result-01",
    );
    expect(state.pendingToolResults.size).toBe(0);
  });

  it("J3: admission rejection creates no user event, restores draft, and stays generation-safe", async () => {
    const backend = new FakeBackend();
    const controller = new ConnectedChatController(backend);
    await selectReady(controller, backend);

    const rejected = controller.send("  Run tests  ", "cmd-rejected");
    backend.prompts[0].fail(new Error("turn_in_progress"));
    await rejected;

    expect(controller.getState().draft).toBe("  Run tests  ");
    expect(controller.getState().timeline.order).toEqual([]);
    expect(controller.getState().status).toBe("error");

    const staleBackend = new FakeBackend();
    const staleController = new ConnectedChatController(staleBackend);
    await selectReady(staleController, staleBackend, "session-A");
    const staleSend = staleController.send("stale prompt", "cmd-stale");
    const selectingB = staleController.selectSession("session-B");
    staleBackend.snapshots[1].resolve(makeSnapshot("session-B", [], FOLLOW_CURSOR));
    await selectingB;
    staleBackend.prompts[0].fail(new Error("late rejection from A"));
    await staleSend;

    expect(staleController.getState().sessionId).toBe("session-B");
    expect(staleController.getState().status).toBe("following");
    expect(staleController.getState().draft).toBe("");
    expect(staleController.getState().timeline.order).toEqual([]);
    controller.dispose();
    staleController.dispose();
  });

  it("J4: owning abort becomes interrupted only after reload, then Resume links the next run", async () => {
    const backend = new FakeBackend();
    const controller = new ConnectedChatController(backend);
    await selectReady(controller, backend);

    const user = fixtureEvent("evt-user-01");
    const interrupted = derivedEvent(fixtureEvent("evt-terminal-interrupted"), {
      source_event_id: "evt-terminal-interrupted-run-01",
      session_seq: "21",
      run_id: "run-01",
    });
    const sending = controller.send("Run tests", "cmd-01");
    backend.prompts[0].push(chatItem(user));
    backend.prompts[0].fail(new DOMException("owning stream lost", "AbortError"));
    await waitUntil(() => backend.snapshots.length === 2);

    expect(controller.getState().durableTerminal).toBeNull();
    backend.snapshots[1].resolve(
      makeSnapshot(SESSION_ID, [user, interrupted], FOLLOW_CURSOR),
    );
    await sending;
    expect(controller.getState().durableTerminal?.outcome).toBe("interrupted");

    const completedRun02 = derivedEvent(fixtureEvent("evt-terminal-completed"), {
      source_event_id: "evt-terminal-completed-run-02",
      session_seq: "22",
      run_id: "run-02",
    });
    const resuming = controller.resume("cmd-02");
    expect(backend.resumeCalls[0]).toEqual({
      sessionId: SESSION_ID,
      request: { command_id: "cmd-02", parent_run_id: "run-01", prompt: null },
    });
    backend.resumes[0].push(chatItem(completedRun02));
    backend.resumes[0].end();
    await waitUntil(() => backend.snapshots.length === 3);
    backend.snapshots[2].resolve(
      makeSnapshot(
        SESSION_ID,
        [user, interrupted, completedRun02],
        FOLLOW_CURSOR,
      ),
    );
    await resuming;

    expect(controller.getState().durableTerminal).toEqual({
      outcome: "completed",
      result: "All tests pass.",
      error: null,
      runId: "run-02",
    });
    controller.dispose();
  });

  it("J5: passive disconnect is non-mutating, reconnect overlap dedupes, and loss is explicit", async () => {
    const backend = new FakeBackend();
    const controller = new ConnectedChatController(backend, {
      sleep: () => Promise.resolve(true),
    });
    await selectReady(controller, backend);

    const assistant = fixtureEvent("evt-assistant-01");
    backend.follows[0].push(chatItem(assistant));
    await flush();
    backend.follows[0].end();
    await waitUntil(() => backend.followCalls.length === 2);

    expect(controller.getState().durableTerminal).toBeNull();
    expect(backend.snapshots).toHaveLength(1);
    expect(backend.cancels).toHaveLength(0);
    backend.follows[1].push(chatItem(assistant));
    backend.follows[1].push(chatItem(assistant));
    await flush();
    expect(controller.getState().timeline.order).toEqual(["evt-assistant-01"]);
    expect(backend.followCalls).toHaveLength(2);

    const lossBackend = new FakeBackend();
    const lossController = new ConnectedChatController(lossBackend);
    await selectReady(lossController, lossBackend);
    const sequenceLoss = fixture.stream_controls.find(
      (frame) => frame.data.reason === "sequence_loss",
    );
    if (!sequenceLoss) throw new Error("fixture is missing sequence_loss");
    lossBackend.follows[0].push({
      type: "stream_control",
      control: parseStreamControl(sequenceLoss.data),
    });
    await flush();
    expect(lossController.getState().status).toBe("replay_required");
    expect(lossController.getState().replayReason).toBe("sequence_loss");
    controller.dispose();
    lossController.dispose();
  });

  it("J6: cancel acknowledgement is non-terminal until one cancelled terminal arrives", async () => {
    const backend = new FakeBackend();
    const controller = new ConnectedChatController(backend);
    await selectReady(controller, backend);

    const cancelling = controller.cancel();
    backend.cancels[0].resolve(parseCancelAck(fixture.http.cancel.response));
    await cancelling;
    expect(controller.getState().status).toBe("cancelling");
    expect(controller.getState().durableTerminal).toBeNull();

    const cancelled = fixtureEvent("evt-terminal-cancelled");
    backend.follows[0].push(chatItem(cancelled));
    backend.follows[0].push(chatItem(cancelled));
    await flush();
    expect(controller.getState().durableTerminal?.outcome).toBe("cancelled");
    expect(
      controller
        .getState()
        .timeline.order.filter(
          (id) => controller.getState().timeline.byId.get(id)?.event.kind === "root_terminal",
        ),
    ).toEqual(["evt-terminal-cancelled"]);
    controller.dispose();
  });

  it("J7: owning transport EOF without root_terminal remains non-terminal after reload", async () => {
    const backend = new FakeBackend();
    const controller = new ConnectedChatController(backend);
    await selectReady(controller, backend);

    const user = fixtureEvent("evt-user-01");
    const sending = controller.send("Run tests", "cmd-01");
    backend.prompts[0].push(chatItem(user));
    backend.prompts[0].end();
    await waitUntil(() => backend.snapshots.length === 2);

    expect(controller.getState().durableTerminal).toBeNull();
    backend.snapshots[1].resolve(makeSnapshot(SESSION_ID, [user], FOLLOW_CURSOR));
    await sending;
    expect(controller.getState().status).toBe("following");
    expect(controller.getState().durableTerminal).toBeNull();
    expect(controller.getState().timeline.order).toEqual(["evt-user-01"]);
    controller.dispose();
  });

  it("J8: same-origin auth-disabled succeeds and missing enabled-auth credentials is checked 401", async () => {
    const origin = "https://console.example";
    const baseUrl = resolveApiBase({}, origin);
    const okFetch = vi.fn(async () => jsonResponse(fixture.http.snapshot.response, 200));
    const client = new ConnectedChatClient({
      baseUrl,
      fetchImpl: okFetch as unknown as typeof fetch,
    });

    const snapshot = await client.snapshot(SESSION_ID, { limit: 2 });
    expect(baseUrl).toBe(origin);
    expect(okFetch).toHaveBeenCalledWith(
      `${origin}/sessions/${SESSION_ID}/chat-events?limit=2`,
      expect.objectContaining({ method: "GET" }),
    );
    expect(snapshot.contract_version).toBe("1.0.0");

    for (const operation of J8_AUTH_OPERATIONS) {
      const authFetch = vi.fn(async () =>
        jsonResponse(AUTH_ERROR.body, AUTH_ERROR.status),
      );
      const protectedClient = new ConnectedChatClient({
        baseUrl,
        fetchImpl: authFetch as unknown as typeof fetch,
      });
      const failure = await operation.run(protectedClient).catch((error: unknown) => error);
      expect(failure, operation.name).toBeInstanceOf(ChatApiError);
      expect(failure, operation.name).not.toBeInstanceOf(ContractViolationError);
      expect((failure as ChatApiError).status, operation.name).toBe(401);
      expect((failure as ChatApiError).error, operation.name).toEqual(CREDENTIALS_REQUIRED);
    }
  });
});
