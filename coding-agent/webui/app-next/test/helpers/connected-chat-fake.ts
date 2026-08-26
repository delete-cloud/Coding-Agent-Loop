// Test-only fake implementing every client seam the connected-chat React
// adapter consumes: the controller stream/REST surface plus the session
// catalog surface (list/create). All responses are controllable deferreds and
// streams so tests decide exactly when the "server" answers.

import type {
  ChatStreamItem,
  CreateSessionRequest,
  PromptRequest,
  ResumeRequest,
} from "@/lib/connected-chat/client";
import type {
  CancelAck,
  ChatEventEnvelope,
  ChatSessionList,
  ChatSnapshot,
  SessionCreated,
} from "@/lib/connected-chat/wire";

export function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function abortError(): DOMException {
  return new DOMException("Aborted", "AbortError");
}

function attachAbort(signal: AbortSignal | undefined, onAbort: () => void) {
  if (!signal) return;
  if (signal.aborted) {
    onAbort();
    return;
  }
  signal.addEventListener("abort", onAbort, { once: true });
}

function abortableDeferred<T>(signal?: AbortSignal) {
  const value = deferred<T>();
  attachAbort(signal, () => value.reject(abortError()));
  return value;
}

export class ControlledStream implements AsyncIterable<ChatStreamItem> {
  private values: ChatStreamItem[] = [];
  private waiters: Array<{
    resolve: (result: IteratorResult<ChatStreamItem>) => void;
    reject: (error: unknown) => void;
  }> = [];
  private terminal: { done: true } | { error: unknown } | null = null;

  constructor(signal?: AbortSignal) {
    if (!signal) return;
    const onAbort = () => {
      this.fail(new DOMException("Aborted", "AbortError"));
    };
    if (signal.aborted) onAbort();
    else signal.addEventListener("abort", onAbort, { once: true });
  }

  get pendingWaiters(): number {
    return this.waiters.length;
  }

  get closed(): boolean {
    return this.terminal !== null;
  }

  push(value: ChatStreamItem) {
    const waiter = this.waiters.shift();
    if (waiter) waiter.resolve({ value, done: false });
    else this.values.push(value);
  }

  end() {
    if (this.terminal) return;
    this.terminal = { done: true };
    for (const waiter of this.waiters.splice(0)) waiter.resolve({ value: undefined, done: true });
  }

  fail(error: unknown) {
    if (this.terminal) return;
    this.terminal = { error };
    for (const waiter of this.waiters.splice(0)) waiter.reject(error);
  }

  [Symbol.asyncIterator](): AsyncIterator<ChatStreamItem> {
    return {
      next: () => {
        if (this.values.length) return Promise.resolve({ value: this.values.shift()!, done: false });
        if (this.terminal && "error" in this.terminal) return Promise.reject(this.terminal.error);
        if (this.terminal) return Promise.resolve({ value: undefined, done: true });
        return new Promise((resolve, reject) => this.waiters.push({ resolve, reject }));
      },
      return: () => {
        this.end();
        return Promise.resolve({ value: undefined, done: true });
      },
    };
  }
}

export function chatItem(event: ChatEventEnvelope): ChatStreamItem {
  return { type: "chat_event", id: event.session_seq, event };
}

export function makeSnapshot(
  sessionId: string,
  events: ChatEventEnvelope[] = [],
  snapshotCursor = "cursor",
): ChatSnapshot {
  return {
    contract_version: "1.0.0",
    session_id: sessionId,
    projection: "connected-chat",
    projection_epoch: "7",
    snapshot_cursor: snapshotCursor,
    next_cursor: null,
    events,
  };
}

export class FakeBackend {
  snapshots: Array<ReturnType<typeof deferred<ChatSnapshot>>> = [];
  follows: ControlledStream[] = [];
  prompts: ControlledStream[] = [];
  resumes: ControlledStream[] = [];
  cancels: Array<ReturnType<typeof deferred<CancelAck>>> = [];
  lists: Array<ReturnType<typeof deferred<ChatSessionList>>> = [];
  creates: Array<ReturnType<typeof deferred<SessionCreated>>> = [];
  followCalls: Array<{ sessionId: string; cursor: string }> = [];
  promptCalls: Array<{ sessionId: string; request: PromptRequest }> = [];
  resumeCalls: Array<{ sessionId: string; request: ResumeRequest }> = [];
  snapshotCalls: Array<{ sessionId: string }> = [];

  snapshot(
    sessionId: string,
    _options: { cursor?: string; limit?: number } = {},
    signal?: AbortSignal,
  ) {
    if (sessionId.length === 0) throw new Error("snapshot requires a sessionId");
    this.snapshotCalls.push({ sessionId });
    const value = abortableDeferred<ChatSnapshot>(signal);
    this.snapshots.push(value);
    return value.promise;
  }

  follow(sessionId: string, cursor: string, signal?: AbortSignal) {
    this.followCalls.push({ sessionId, cursor });
    const stream = new ControlledStream(signal);
    this.follows.push(stream);
    return stream;
  }

  prompt(sessionId: string, request: PromptRequest, signal?: AbortSignal) {
    this.promptCalls.push({ sessionId, request });
    const stream = new ControlledStream(signal);
    this.prompts.push(stream);
    return stream;
  }

  resume(sessionId: string, request: ResumeRequest, signal?: AbortSignal) {
    this.resumeCalls.push({ sessionId, request });
    const stream = new ControlledStream(signal);
    this.resumes.push(stream);
    return stream;
  }

  cancel(_sessionId?: string, signal?: AbortSignal) {
    const value = abortableDeferred<CancelAck>(signal);
    this.cancels.push(value);
    return value.promise;
  }

  listSessions(signal?: AbortSignal) {
    const value = abortableDeferred<ChatSessionList>(signal);
    this.lists.push(value);
    return value.promise;
  }

  createSession(request: CreateSessionRequest, signal?: AbortSignal) {
    void request;
    const value = abortableDeferred<SessionCreated>(signal);
    this.creates.push(value);
    return value.promise;
  }
}

export async function flush() {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

export async function waitUntil(
  predicate: () => boolean,
  description = "condition",
  timeoutMs = 2000,
) {
  const started = Date.now();
  while (true) {
    if (predicate()) return;
    if (Date.now() - started > timeoutMs) {
      throw new Error(`Timeout waiting for ${description} after ${timeoutMs}ms`);
    }
    await flush();
    await new Promise<void>((resolve) => {
      setTimeout(resolve, 10);
    });
  }
}
