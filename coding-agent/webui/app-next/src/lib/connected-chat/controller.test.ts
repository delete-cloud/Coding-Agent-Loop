import { describe, expect, it, vi } from "vitest";

import fixture from "../../../test/fixtures/connected-chat/v1/connected-chat-contract.json";
import { ChatApiError, type ChatStreamItem, type PromptRequest, type ResumeRequest } from "./client";
import {
  ConnectedChatController,
  defaultReconnectDelayMs,
  type ConnectedChatControllerClient,
} from "./controller";
import { parseCancelAck, parseStreamControl, type CancelAck, type ChatEventEnvelope, type ChatSnapshot } from "./wire";

const events = fixture.events.map((entry) => entry.data as ChatEventEnvelope);
const cursor = fixture.http.follow.cursor;

function fixtureCursorExpired(): ChatApiError {
  const expired = fixture.cursor.errors.find((entry) => entry.case === "expired");
  if (!expired) throw new Error("fixture missing expired cursor case");
  return new ChatApiError(expired.status, {
    code: expired.reason,
    message: "cursor expired",
    retryable: false,
    replay_required: expired.replay_required,
  });
}

function fixtureCredentialsRequired(): ChatApiError {
  const auth = fixture.http.errors.auth[0];
  if (!auth) throw new Error("fixture missing credentials_required auth case");
  return new ChatApiError(auth.status, auth.body.error);
}

function fixtureRetryableAdmission(): ChatApiError {
  const admission = fixture.http.errors.admission[0];
  if (!admission) throw new Error("fixture missing retryable admission case");
  return new ChatApiError(admission.status, admission.body.error);
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

class ControlledStream implements AsyncIterable<ChatStreamItem> {
  private values: ChatStreamItem[] = [];
  private waiters: Array<{ resolve: (result: IteratorResult<ChatStreamItem>) => void; reject: (error: unknown) => void }> = [];
  private terminal: { done: true } | { error: unknown } | null = null;
  push(value: ChatStreamItem) { const waiter = this.waiters.shift(); waiter ? waiter.resolve({ value, done: false }) : this.values.push(value); }
  end() { this.terminal = { done: true }; for (const waiter of this.waiters.splice(0)) waiter.resolve({ value: undefined, done: true }); }
  fail(error: unknown) { this.terminal = { error }; for (const waiter of this.waiters.splice(0)) waiter.reject(error); }
  [Symbol.asyncIterator](): AsyncIterator<ChatStreamItem> {
    return { next: () => {
      if (this.values.length) return Promise.resolve({ value: this.values.shift()!, done: false });
      if (this.terminal && "error" in this.terminal) return Promise.reject(this.terminal.error);
      if (this.terminal) return Promise.resolve({ value: undefined, done: true });
      return new Promise((resolve, reject) => this.waiters.push({ resolve, reject }));
    } };
  }
}

function snapshot(sessionId: string, snapshotEvents: ChatEventEnvelope[] = [], snapshotCursor = cursor): ChatSnapshot {
  return { contract_version: "1.1.0", session_id: sessionId, projection: "connected-chat", projection_epoch: "7", snapshot_cursor: snapshotCursor, next_cursor: null, events: snapshotEvents };
}

function item(event: ChatEventEnvelope): ChatStreamItem { return { type: "chat_event", id: event.session_seq, event }; }

class FakeClient implements ConnectedChatControllerClient {
  snapshots: Array<ReturnType<typeof deferred<ChatSnapshot>>> = [];
  follows: ControlledStream[] = [];
  prompts: ControlledStream[] = [];
  resumes: ControlledStream[] = [];
  cancels: Array<ReturnType<typeof deferred<CancelAck>>> = [];
  followCalls: Array<{ sessionId: string; cursor: string }> = [];
  promptCalls: Array<{ sessionId: string; request: PromptRequest }> = [];
  resumeCalls: Array<{ sessionId: string; request: ResumeRequest }> = [];
  snapshot(_sessionId: string, _options?: { cursor?: string; limit?: number }, signal?: AbortSignal) {
    const value = deferred<ChatSnapshot>();
    this.snapshots.push(value);
    signal?.addEventListener("abort", () => {
      value.reject(new DOMException("Aborted", "AbortError"));
    }, { once: true });
    return value.promise;
  }
  follow(sessionId: string, value: string) { this.followCalls.push({ sessionId, cursor: value }); const stream = new ControlledStream(); this.follows.push(stream); return stream; }
  prompt(sessionId: string, request: PromptRequest) { this.promptCalls.push({ sessionId, request }); const stream = new ControlledStream(); this.prompts.push(stream); return stream; }
  resume(sessionId: string, request: ResumeRequest) { this.resumeCalls.push({ sessionId, request }); const stream = new ControlledStream(); this.resumes.push(stream); return stream; }
  cancel() { const value = deferred<CancelAck>(); this.cancels.push(value); return value.promise; }
}

async function flush() { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); }
async function waitUntil(predicate: () => boolean) {
  for (let attempt = 0; attempt < 20; attempt += 1) { if (predicate()) return; await flush(); }
  throw new Error("condition was not reached");
}

async function selectReady(controller: ConnectedChatController, client: FakeClient, sessionId = "session-01", snapshotCursor = cursor) {
  const selected = controller.selectSession(sessionId);
  client.snapshots.at(-1)!.resolve(snapshot(sessionId, [], snapshotCursor));
  await selected;
  await flush();
}

describe("ConnectedChatController", () => {
  it("loads a snapshot before passively following its last safe cursor", async () => {
    const client = new FakeClient(); const controller = new ConnectedChatController(client);
    const selected = controller.selectSession("session-01");
    expect(client.followCalls).toEqual([]);
    client.snapshots[0].resolve(snapshot("session-01", [events[0]])); await selected; await flush();
    expect(controller.getState().timeline.order).toEqual(["evt-user-01"]);
    expect(client.followCalls).toEqual([{ sessionId: "session-01", cursor }]);
  });

  it("reconnects passive EOF from the last safe cursor without inventing terminal truth", async () => {
    const client = new FakeClient();
    const controller = new ConnectedChatController(client, { sleep: () => Promise.resolve(true) });
    await selectReady(controller, client);
    client.follows[0].push(item(events[1])); await flush(); client.follows[0].end();
    await waitUntil(() => client.followCalls.length === 2);
    expect(controller.getState().durableTerminal).toBeNull();
    expect(client.followCalls.at(-1)).toEqual({ sessionId: "session-01", cursor });
  });

  it.each(fixture.stream_controls)(
    "enters replay-required for $data.reason and preserves the server's safe cursor",
    async (frame) => {
      const client = new FakeClient(); const controller = new ConnectedChatController(client); await selectReady(controller, client);
      client.follows[0].push({ type: "stream_control", control: parseStreamControl(frame.data) }); await flush();
      expect(controller.getState().status).toBe("replay_required");
      expect(controller.getState().replayReason).toBe(frame.data.reason);
      expect(controller.getState().lastSafeCursor).toBe(frame.data.cursor);
      expect(client.followCalls).toHaveLength(1);
    },
  );

  it.each(fixture.stream_controls)(
    "preserves owning send replay-required reason and the server's exact safe cursor for $data.reason",
    async (frame) => {
      const client = new FakeClient(); const controller = new ConnectedChatController(client);
      await selectReady(controller, client, "session-01", "snapshot-cursor-distinct-from-control-cursor");
      const sent = controller.send("Run tests", "cmd-01");
      client.prompts[0].push({ type: "stream_control", control: parseStreamControl(frame.data) });
      await waitUntil(() => controller.getState().status === "replay_required");
      expect(controller.getState().replayReason).toBe(frame.data.reason);
      expect(controller.getState().lastSafeCursor).toBe(frame.data.cursor);
      expect(client.snapshots).toHaveLength(1);
      await sent;
    },
  );

  it("preserves owning resume replay-required reason and the server's exact safe cursor", async () => {
    const client = new FakeClient(); const controller = new ConnectedChatController(client);
    await selectReady(controller, client, "session-01", "snapshot-cursor-distinct-from-control-cursor");
    client.follows[0].push(item(events[7])); await flush();
    const control = fixture.stream_controls[0].data;
    const resumed = controller.resume("cmd-02");
    client.resumes[0].push({ type: "stream_control", control: parseStreamControl(control) });
    await waitUntil(() => controller.getState().status === "replay_required");
    expect(controller.getState().replayReason).toBe(control.reason);
    expect(controller.getState().lastSafeCursor).toBe(control.cursor);
    expect(client.snapshots).toHaveLength(1);
    await resumed;
  });

  it("backs off passive reconnects with the injected delay and retains the transport error", async () => {
    const attempts: number[] = [];
    const sleeps: Array<{ ms: number; resolve: (completed: boolean) => void }> = [];
    const client = new FakeClient();
    const controller = new ConnectedChatController(client, {
      reconnectDelayMs: (attempt) => { attempts.push(attempt); return 100 * (attempt + 1); },
      sleep: (ms) => new Promise((resolve) => { sleeps.push({ ms, resolve }); }),
    });
    await selectReady(controller, client);
    const failure = new Error("follow transport down");
    client.follows[0].fail(failure);
    await waitUntil(() => sleeps.length === 1);
    expect(controller.getState().status).toBe("reconnecting");
    expect(controller.getState().error).toBe(failure);
    expect(sleeps[0].ms).toBe(100);
    expect(client.followCalls).toHaveLength(1);
    sleeps[0].resolve(true);
    await waitUntil(() => client.followCalls.length === 2);
    expect(client.followCalls[1]).toEqual({ sessionId: "session-01", cursor });
    expect(controller.getState().error).toBe(failure);
    const secondFailure = new Error("follow transport still down");
    client.follows[1].fail(secondFailure);
    await waitUntil(() => sleeps.length === 2);
    expect(sleeps[1].ms).toBe(200);
    expect(attempts).toEqual([0, 1]);
    expect(controller.getState().error).toBe(secondFailure);
  });

  it("restores following and clears the transport error on the first event after a delayed reconnect", async () => {
    const sleeps: Array<{ ms: number; resolve: (completed: boolean) => void }> = [];
    const client = new FakeClient();
    const controller = new ConnectedChatController(client, {
      sleep: (ms) => new Promise((resolve) => { sleeps.push({ ms, resolve }); }),
    });
    await selectReady(controller, client);
    client.follows[0].push(item(events[0])); await flush();
    const failure = new Error("follow transport down");
    client.follows[0].fail(failure);
    await waitUntil(() => sleeps.length === 1);
    expect(controller.getState().status).toBe("reconnecting");
    expect(controller.getState().error).toBe(failure);
    expect(client.followCalls).toHaveLength(1);
    sleeps[0].resolve(true);
    await waitUntil(() => client.followCalls.length === 2);
    expect(client.followCalls[1]).toEqual({ sessionId: "session-01", cursor });
    expect(controller.getState().status).toBe("reconnecting");
    expect(controller.getState().error).toBe(failure);
    client.follows[1].push(item(events[1])); await flush();
    expect(controller.getState().status).toBe("following");
    expect(controller.getState().error).toBeNull();
    expect(controller.getState().timeline.order).toEqual(["evt-user-01", "evt-thinking-01"]);
    expect(controller.getState().lastSafeCursor).toBe(cursor);
    expect(controller.getState().durableTerminal).toBeNull();
  });

  it("delays passive reconnects with the default timer-backed backoff", async () => {
    vi.useFakeTimers();
    try {
      const client = new FakeClient(); const controller = new ConnectedChatController(client);
      await selectReady(controller, client);
      client.follows[0].fail(new Error("down"));
      await waitUntil(() => controller.getState().status === "reconnecting");
      await vi.advanceTimersByTimeAsync(249);
      expect(client.followCalls).toHaveLength(1);
      await vi.advanceTimersByTimeAsync(1);
      await waitUntil(() => client.followCalls.length === 2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("bounds the default reconnect delay with exponential backoff", () => {
    expect([0, 1, 2, 3, 4, 5, 6, 20].map(defaultReconnectDelayMs)).toEqual([250, 500, 1000, 2000, 4000, 5000, 5000, 5000]);
  });

  it("does not reconnect a follow aborted by reselection", async () => {
    const client = new FakeClient();
    const controller = new ConnectedChatController(client, { sleep: () => Promise.resolve(true) });
    await selectReady(controller, client, "session-01");
    const reselected = controller.selectSession("session-02");
    client.follows[0].end();
    client.snapshots[1].resolve(snapshot("session-02"));
    await reselected; await flush();
    expect(client.followCalls.filter((call) => call.sessionId === "session-01")).toHaveLength(1);
  });

  it("restores the exact draft when prompt admission rejects", async () => {
    const client = new FakeClient(); const controller = new ConnectedChatController(client); await selectReady(controller, client);
    const sent = controller.send("  Run tests  ", "cmd-01"); client.prompts[0].fail(new Error("rejected")); await sent;
    expect(controller.getState().draft).toBe("  Run tests  ");
    expect(controller.getState().timeline.order).toEqual([]);
    expect(controller.getState().status).toBe("error");
  });

  it("clears an unchanged sent draft after a pre-frame owning transport failure", async () => {
    const client = new FakeClient();
    const controller = new ConnectedChatController(client);
    await selectReady(controller, client);
    const sent = controller.send("Run tests", "cmd-01");
    client.prompts[0].fail(new TypeError("network"));
    await waitUntil(() => client.snapshots.length === 2);
    client.snapshots[1].resolve(snapshot("session-01", [events[0]]));
    await sent;
    expect(controller.getState().draft).toBe("");
    expect(controller.getState().timeline.order).toContain("evt-user-01");
  });

  it("keeps an unsent draft across same-session reload", async () => {
    const client = new FakeClient();
    const controller = new ConnectedChatController(client);
    await selectReady(controller, client);
    controller.setDraft("unsent note");
    const reloaded = controller.selectSession("session-01");
    client.snapshots.at(-1)!.resolve(snapshot("session-01", [events[0]]));
    await reloaded;
    await flush();
    expect(controller.getState().draft).toBe("unsent note");
    expect(controller.getState().sessionId).toBe("session-01");
  });

  it("clears a draft only after canonical admission is observed", async () => {
    const client = new FakeClient(); const controller = new ConnectedChatController(client); await selectReady(controller, client);
    const sent = controller.send("Run tests", "cmd-01"); expect(controller.getState().draft).toBe("Run tests");
    client.prompts[0].push(item(events[0])); await flush(); expect(controller.getState().draft).toBe("");
    client.prompts[0].push(item(events[6])); client.prompts[0].end();
    await waitUntil(() => client.snapshots.length === 2);
    client.snapshots[1].resolve(snapshot("session-01", [events[6]])); await sent;
    expect(controller.getState().durableTerminal?.outcome).toBe("completed");
  });

  it("reloads canonically after owning EOF and never guesses interrupted", async () => {
    const client = new FakeClient(); const controller = new ConnectedChatController(client); await selectReady(controller, client);
    const sent = controller.send("Run tests", "cmd-01"); client.prompts[0].push(item(events[0])); await flush(); client.prompts[0].end(); await flush();
    expect(controller.getState().durableTerminal).toBeNull();
    expect(client.snapshots).toHaveLength(2);
    client.snapshots[1].resolve(snapshot("session-01", [events[9]])); await sent;
    expect(controller.getState().status).toBe("following");
    expect(controller.getState().durableTerminal).toEqual({ outcome: "interrupted", result: null, error: null, runId: "run-04" });
  });

  it("reloads canonically after an admitted owning stream abort", async () => {
    const client = new FakeClient(); const controller = new ConnectedChatController(client); await selectReady(controller, client);
    const sent = controller.send("Run tests", "cmd-01"); client.prompts[0].push(item(events[0])); await flush();
    client.prompts[0].fail(new DOMException("connection lost", "AbortError")); await flush();
    expect(controller.getState().durableTerminal).toBeNull();
    expect(client.snapshots).toHaveLength(2);
    client.snapshots[1].resolve(snapshot("session-01", [events[9]])); await sent;
    expect(controller.getState().status).toBe("following");
    expect(controller.getState().durableTerminal?.outcome).toBe("interrupted");
  });

  it("cancel acknowledgement is non-terminal until a canonical terminal event arrives", async () => {
    const client = new FakeClient(); const controller = new ConnectedChatController(client); await selectReady(controller, client);
    const cancelling = controller.cancel(); client.cancels[0].resolve(parseCancelAck(fixture.http.cancel.response)); await cancelling;
    expect(controller.getState().status).toBe("cancelling"); expect(controller.getState().durableTerminal).toBeNull();
    client.follows[0].push(item(events[8])); await flush(); expect(controller.getState().durableTerminal?.outcome).toBe("cancelled");
  });

  it("permits Resume only from durable interrupted, failed, or cancelled state", async () => {
    const client = new FakeClient(); const controller = new ConnectedChatController(client); await selectReady(controller, client);
    await expect(controller.resume("cmd-02")).rejects.toThrow(/durable/);
    client.follows[0].push(item(events[7])); await flush(); const resumed = controller.resume("cmd-02");
    expect(client.resumeCalls[0].request.parent_run_id).toBe("run-02"); client.resumes[0].end();
    await waitUntil(() => client.snapshots.length === 2);
    client.snapshots[1].resolve(snapshot("session-01", [events[7]])); await resumed;
    expect(controller.getState().status).toBe("following");
  });

  it("reloads canonically after an admitted resume stream abort", async () => {
    const client = new FakeClient(); const controller = new ConnectedChatController(client); await selectReady(controller, client);
    client.follows[0].push(item(events[7])); await flush();
    const resumed = controller.resume("cmd-02");
    client.resumes[0].push(item(events[1])); await flush();
    client.resumes[0].fail(new DOMException("connection lost", "AbortError"));
    await waitUntil(() => client.snapshots.length === 2);
    client.snapshots[1].resolve(snapshot("session-01", [events[7]])); await resumed;
    expect(controller.getState().status).toBe("following");
    expect(controller.getState().durableTerminal).toEqual({ outcome: "failed", result: null, error: { code: "adapter_failed", message: "Adapter failed" }, runId: "run-02" });
  });

  it("surfaces resume admission rejection as an error without a canonical reload", async () => {
    const client = new FakeClient(); const controller = new ConnectedChatController(client); await selectReady(controller, client);
    client.follows[0].push(item(events[7])); await flush();
    const resumed = controller.resume("cmd-02");
    client.resumes[0].fail(new Error("rejected")); await resumed;
    expect(controller.getState().status).toBe("error");
    expect(client.snapshots).toHaveLength(1);
  });

  it("keeps a locally aborted resume silent", async () => {
    const client = new FakeClient(); const controller = new ConnectedChatController(client); await selectReady(controller, client);
    client.follows[0].push(item(events[7])); await flush();
    const resumed = controller.resume("cmd-02");
    const sent = controller.send("new prompt", "cmd-03");
    client.resumes[0].fail(new DOMException("aborted", "AbortError")); await resumed;
    expect(controller.getState().status).toBe("sending");
    expect(controller.getState().error).toBeNull();
    client.prompts[0].end();
    await waitUntil(() => client.snapshots.length === 2);
    client.snapshots[1].resolve(snapshot("session-01", [events[6]])); await sent;
    expect(controller.getState().status).toBe("following");
  });

  it("generation-gates late snapshot and follow activity after selecting B", async () => {
    const client = new FakeClient(); const controller = new ConnectedChatController(client);
    const a = controller.selectSession("A"); const b = controller.selectSession("B");
    client.snapshots[1].resolve(snapshot("B")); await b; client.snapshots[0].resolve(snapshot("A", [events[0]])); await a; await flush();
    expect(controller.getState().sessionId).toBe("B"); expect(controller.getState().timeline.order).toEqual([]);
  });

  it("generation-gates stale same-session prompt, cancel, and reconciliation responses", async () => {
    const client = new FakeClient(); const controller = new ConnectedChatController(client); await selectReady(controller, client, "session-01");
    const sent = controller.send("stale draft", "cmd-a"); const cancelled = controller.cancel(); const reselected = controller.selectSession("session-01");
    client.snapshots[1].resolve(snapshot("session-01", [events[1]])); await reselected;
    client.prompts[0].push(item(events[0])); client.prompts[0].end();
    await Promise.all([sent, cancelled]); await flush();
    expect(client.cancels).toHaveLength(0);
    expect(client.snapshots).toHaveLength(2);
    expect(controller.getState().sessionId).toBe("session-01");
    expect(controller.getState().timeline.order).toEqual(["evt-thinking-01"]);
    expect(controller.getState().draft).toBe("");
    expect(controller.getState().status).toBe("following");
    expect(controller.getState().durableTerminal).toBeNull();
  });

  it("dedupes snapshot and live overlap by source event id", async () => {
    const client = new FakeClient(); const controller = new ConnectedChatController(client);
    const selected = controller.selectSession("session-01");
    client.snapshots[0].resolve(snapshot("session-01", [events[5]])); await selected; await flush();
    client.follows[0].push(item(events[5])); client.follows[0].push(item(events[6])); await flush();
    expect(controller.getState().timeline.order).toEqual(["evt-assistant-01", "evt-terminal-completed"]);
    expect(controller.getState().timeline.byId.size).toBe(2);
  });

  it("stops notifications and ignores late snapshot state after unsubscribe and dispose", async () => {
    const client = new FakeClient(); const controller = new ConnectedChatController(client); let notifications = 0;
    const unsubscribe = controller.subscribe(() => notifications++); const selected = controller.selectSession("A"); expect(notifications).toBe(1);
    unsubscribe(); controller.dispose(); client.snapshots[0].resolve(snapshot("A", [events[0]])); await selected;
    expect(notifications).toBe(1);
    expect(controller.getState().sessionId).toBe("A"); expect(controller.getState().timeline.order).toEqual([]);
    expect(client.followCalls).toEqual([]);
  });

  it("setDraft updates the draft and notifies subscribers", async () => {
    const client = new FakeClient(); const controller = new ConnectedChatController(client); let notifications = 0;
    controller.subscribe(() => notifications++); await selectReady(controller, client);
    notifications = 0;
    controller.setDraft("half-typed prompt");
    expect(controller.getState().draft).toBe("half-typed prompt");
    expect(notifications).toBe(1);
    controller.setDraft("");
    expect(controller.getState().draft).toBe("");
    expect(notifications).toBe(2);
  });

  it("setDraft on a disposed controller throws", async () => {
    const client = new FakeClient(); const controller = new ConnectedChatController(client);
    controller.dispose();
    expect(() => controller.setDraft("x")).toThrow(/disposed/);
  });

  it("refuses send during replay_required and starts no owning stream", async () => {
    const client = new FakeClient();
    const controller = new ConnectedChatController(client);
    await selectReady(controller, client);
    const control = parseStreamControl(fixture.stream_controls[0].data);
    client.follows[0].push({ type: "stream_control", control });
    await flush();
    expect(controller.getState().status).toBe("replay_required");

    const pending = controller.send("Run tests", "cmd-01");
    expect(client.promptCalls).toHaveLength(0);
    await expect(pending).rejects.toThrow(/replay is required/);
    expect(client.snapshots).toHaveLength(1);
    expect(controller.getState().status).toBe("replay_required");
    expect(controller.getState().replayReason).toBe(control.reason);
    expect(controller.getState().lastSafeCursor).toBe(control.cursor);
  });

  it("refuses resume during replay_required even when a durable terminal exists", async () => {
    const client = new FakeClient();
    const controller = new ConnectedChatController(client);
    await selectReady(controller, client);
    client.follows[0].push(item(events[7]));
    await flush();
    const control = parseStreamControl(fixture.stream_controls[0].data);
    client.follows[0].push({ type: "stream_control", control });
    await flush();
    expect(controller.getState().durableTerminal?.outcome).toBe("failed");
    expect(controller.getState().status).toBe("replay_required");

    const pending = controller.resume("cmd-02");
    expect(client.resumeCalls).toHaveLength(0);
    await expect(pending).rejects.toThrow(/replay is required/);
    expect(client.snapshots).toHaveLength(1);
    expect(controller.getState().status).toBe("replay_required");
  });

  it("reloads a replay_required session into a live follow and then allows send", async () => {
    const client = new FakeClient();
    const controller = new ConnectedChatController(client);
    await selectReady(controller, client);
    client.follows[0].push({ type: "stream_control", control: parseStreamControl(fixture.stream_controls[0].data) });
    await flush();
    expect(controller.getState().status).toBe("replay_required");

    const reloaded = controller.selectSession("session-01");
    client.snapshots.at(-1)!.resolve(snapshot("session-01", [events[0]]));
    await reloaded;
    await flush();

    expect(controller.getState().status).toBe("following");
    expect(controller.getState().replayReason).toBeNull();
    expect(controller.getState().timeline.order).toEqual(["evt-user-01"]);
    expect(client.followCalls).toHaveLength(2);

    const sent = controller.send("Run tests", "cmd-01");
    expect(client.promptCalls).toHaveLength(1);
    client.prompts[0].end();
    await waitUntil(() => client.snapshots.length === 3);
    client.snapshots[2].resolve(snapshot("session-01", [events[0]]));
    await sent;
    expect(controller.getState().status).toBe("following");
  });

  it("enters replay_required on fixture cursor_expired and never retries the same cursor", async () => {
    const sleeps: Array<{ ms: number; resolve: (completed: boolean) => void }> = [];
    const client = new FakeClient();
    const controller = new ConnectedChatController(client, {
      sleep: (ms) => new Promise((resolve) => { sleeps.push({ ms, resolve }); }),
    });
    await selectReady(controller, client);
    const expired = fixtureCursorExpired();
    client.follows[0].fail(expired);
    await waitUntil(() => controller.getState().status !== "following");

    expect(controller.getState().status).toBe("replay_required");
    expect(controller.getState().replayReason).toBe("cursor_expired");
    expect(controller.getState().error).toBe(expired);
    expect(controller.getState().lastSafeCursor).toBe(cursor);
    expect(client.followCalls).toHaveLength(1);
    expect(sleeps).toHaveLength(0);

    const pending = controller.send("Run tests", "cmd-01");
    expect(client.promptCalls).toHaveLength(0);
    await expect(pending).rejects.toThrow(/replay is required/);
  });

  it("surfaces fixture credentials_required 401 as a stable error and never retries", async () => {
    const sleeps: Array<{ ms: number; resolve: (completed: boolean) => void }> = [];
    const client = new FakeClient();
    const controller = new ConnectedChatController(client, {
      sleep: (ms) => new Promise((resolve) => { sleeps.push({ ms, resolve }); }),
    });
    await selectReady(controller, client);
    const unauthorized = fixtureCredentialsRequired();
    client.follows[0].fail(unauthorized);
    await waitUntil(() => controller.getState().status !== "following");

    expect(controller.getState().status).toBe("error");
    expect(controller.getState().error).toBe(unauthorized);
    expect((controller.getState().error as ChatApiError).error.code).toBe("credentials_required");
    expect(client.followCalls).toHaveLength(1);
    expect(sleeps).toHaveLength(0);
  });

  it("keeps bounded reconnect for a retryable checked ChatApiError", async () => {
    const sleeps: Array<{ ms: number; resolve: (completed: boolean) => void }> = [];
    const client = new FakeClient();
    const controller = new ConnectedChatController(client, {
      sleep: (ms) => new Promise((resolve) => { sleeps.push({ ms, resolve }); }),
    });
    await selectReady(controller, client);
    const retryable = fixtureRetryableAdmission();
    expect(retryable.error.retryable).toBe(true);
    client.follows[0].fail(retryable);
    await waitUntil(() => sleeps.length === 1);

    expect(controller.getState().status).toBe("reconnecting");
    expect(controller.getState().error).toBe(retryable);
    expect(client.followCalls).toHaveLength(1);
    sleeps[0].resolve(true);
    await waitUntil(() => client.followCalls.length === 2);
    expect(client.followCalls[1]).toEqual({ sessionId: "session-01", cursor });
  });

  it("reloads after a cursor_expired follow error and restores a live follow", async () => {
    const sleeps: Array<{ resolve: (completed: boolean) => void }> = [];
    const client = new FakeClient();
    const controller = new ConnectedChatController(client, {
      sleep: () => new Promise((resolve) => { sleeps.push({ resolve }); }),
    });
    await selectReady(controller, client);
    client.follows[0].fail(fixtureCursorExpired());
    await waitUntil(() => controller.getState().status === "replay_required");
    expect(client.followCalls).toHaveLength(1);
    expect(sleeps).toHaveLength(0);

    const reloaded = controller.selectSession("session-01");
    client.snapshots.at(-1)!.resolve(snapshot("session-01", [events[0]]));
    await reloaded;
    await flush();

    expect(controller.getState().status).toBe("following");
    expect(controller.getState().replayReason).toBeNull();
    expect(controller.getState().error).toBeNull();
    expect(client.followCalls).toHaveLength(2);
  });

  it("does not replace sending with reconnecting when passive follow fails", async () => {
    const client = new FakeClient();
    const controller = new ConnectedChatController(client, {
      sleep: () => new Promise(() => undefined),
    });
    await selectReady(controller, client);
    const sending = controller.send("hello", "cmd-own");
    await waitUntil(() => client.promptCalls.length === 1);
    client.follows[0].fail(new Error("follow dropped"));
    await flush();
    expect(controller.getState().status).toBe("sending");
    client.prompts[0].end();
    await waitUntil(() => client.snapshots.length === 2);
    client.snapshots[1].resolve(snapshot("session-01", [events[0]]));
    await sending;
    expect(controller.getState().status).toBe("following");
  });

  it("keeps follow events that arrive while canonical snapshot is in flight", async () => {
    const client = new FakeClient();
    const controller = new ConnectedChatController(client);
    await selectReady(controller, client);
    const sending = controller.send("hello", "cmd-merge");
    await waitUntil(() => client.promptCalls.length === 1);
    client.prompts[0].push(item(events[0]));
    client.follows[0].push(item(events[5]));
    await flush();
    client.prompts[0].end();
    await waitUntil(() => client.snapshots.length === 2);
    client.snapshots.at(-1)!.resolve(snapshot("session-01", [events[0]]));
    await sending;
    const ids = controller.getState().timeline.order;
    expect(ids).toContain(events[0].source_event_id);
    expect(ids).toContain(events[5].source_event_id);
  });

  it("errors instead of staying sending when finalization snapshot never returns", async () => {
    const client = new FakeClient();
    const controller = new ConnectedChatController(client, { finalizeTimeoutMs: 0 });
    await selectReady(controller, client);
    const sending = controller.send("hello", "cmd-timeout");
    await waitUntil(() => client.promptCalls.length === 1);
    client.prompts[0].end();
    await sending;
    expect(controller.getState().status).toBe("error");
  });

});
