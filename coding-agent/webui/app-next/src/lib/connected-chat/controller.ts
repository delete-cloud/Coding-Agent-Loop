import {
  ChatApiError,
  type ChatStreamItem,
  type PromptRequest,
  type ResumeRequest,
} from "./client";
import {
  createTimelineState,
  reduceChatEvent,
  type TimelineState,
} from "./timeline";
import type {
  CancelAck,
  ChatSnapshot,
  RootTerminalPayload,
} from "./wire";

export type ConnectedChatStatus =
  | "idle"
  | "loading"
  | "following"
  | "sending"
  | "cancelling"
  | "reconnecting"
  | "replay_required"
  | "error";

export interface DurableTerminal extends RootTerminalPayload {
  runId: string;
}

export interface ConnectedChatState {
  sessionId: string | null;
  status: ConnectedChatStatus;
  timeline: TimelineState;
  lastSafeCursor: string | null;
  replayReason: string | null;
  durableTerminal: DurableTerminal | null;
  draft: string;
  error: unknown | null;
}

export interface ConnectedChatControllerClient {
  snapshot(
    sessionId: string,
    options?: { cursor?: string; limit?: number },
    signal?: AbortSignal,
  ): Promise<ChatSnapshot>;
  follow(sessionId: string, cursor: string, signal?: AbortSignal): AsyncIterable<ChatStreamItem>;
  prompt(sessionId: string, request: PromptRequest, signal?: AbortSignal): AsyncIterable<ChatStreamItem>;
  resume(sessionId: string, request: ResumeRequest, signal?: AbortSignal): AsyncIterable<ChatStreamItem>;
  cancel(sessionId: string, signal?: AbortSignal): Promise<CancelAck>;
}

type Listener = () => void;
type StreamKind = "passive" | "owning";

interface ConsumeOutcome {
  observedEvent: boolean;
  replayControl: boolean;
}

export interface ConnectedChatControllerOptions {
  /** Delay before passive reconnect attempt N (0-based). Defaults to bounded exponential backoff. */
  reconnectDelayMs?: (attempt: number) => number;
  /** Sleep seam for reconnect backoff; resolves false when the signal aborts mid-sleep. */
  sleep?: (delayMs: number, signal: AbortSignal) => Promise<boolean>;
  /** Bound for canonical snapshot after an owning stream EOF. */
  finalizeTimeoutMs?: number;
}

export function defaultReconnectDelayMs(attempt: number): number {
  return Math.min(250 * 2 ** attempt, 5000);
}

function defaultSleep(delayMs: number, signal: AbortSignal): Promise<boolean> {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve(false);
      return;
    }
    const onAbort = () => {
      clearTimeout(timer);
      resolve(false);
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve(true);
    }, delayMs);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

function initialState(): ConnectedChatState {
  return {
    sessionId: null,
    status: "idle",
    timeline: createTimelineState(),
    lastSafeCursor: null,
    replayReason: null,
    durableTerminal: null,
    draft: "",
    error: null,
  };
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export class ConnectedChatController {
  private state = initialState();
  private readonly listeners = new Set<Listener>();
  private generation = 0;
  private operation = 0;
  private disposed = false;
  private selectionAbort: AbortController | null = null;
  private owningAbort: AbortController | null = null;
  private cancelAbort: AbortController | null = null;
  private passiveFollowRunning = false;
  private pendingPassiveFollow:
    | { sessionId: string; cursor: string; generation: number; abort: AbortController }
    | null = null;
  private readonly reconnectDelayMs: (attempt: number) => number;
  private readonly sleep: (delayMs: number, signal: AbortSignal) => Promise<boolean>;
  private readonly finalizeTimeoutMs: number;

  constructor(
    private readonly client: ConnectedChatControllerClient,
    options: ConnectedChatControllerOptions = {},
  ) {
    this.reconnectDelayMs = options.reconnectDelayMs ?? defaultReconnectDelayMs;
    this.sleep = options.sleep ?? defaultSleep;
    this.finalizeTimeoutMs = options.finalizeTimeoutMs ?? 10_000;
  }

  getState(): ConnectedChatState {
    return this.state;
  }

  subscribe(listener: Listener): () => void {
    if (this.disposed) throw new Error("cannot subscribe to a disposed controller");
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  async selectSession(sessionId: string): Promise<void> {
    if (this.disposed) throw new Error("cannot select a session on a disposed controller");
    if (sessionId.length === 0) throw new Error("sessionId must be non-empty");
    const generation = ++this.generation;
    const operation = ++this.operation;
    this.abortOwnedOperations();
    const abort = new AbortController();
    this.selectionAbort = abort;
    this.replace({
      ...initialState(),
      sessionId,
      status: "loading",
    });
    await this.loadCanonical(sessionId, generation, operation, abort, true);
  }

  async send(prompt: string, commandId: string): Promise<void> {
    const sessionId = this.requireSession();
    if (commandId.length === 0) throw new Error("commandId must be non-empty");
    this.assertSendable("send");
    const generation = this.generation;
    const operation = ++this.operation;
    this.owningAbort?.abort();
    const abort = new AbortController();
    this.owningAbort = abort;
    this.patch({ status: "sending", draft: prompt, error: null });
    let admitted = false;
    let replayControl = false;
    try {
      const outcome = await this.consumeStream(
        this.client.prompt(sessionId, { prompt, command_id: commandId }, abort.signal),
        generation,
        operation,
        "owning",
        () => {
          admitted = true;
          this.patch({ draft: "" });
        },
      );
      replayControl = outcome.replayControl;
    } catch (error) {
      if (!this.isCurrent(generation, operation)) return;
      if (admitted) {
        await this.reconcileOwningEof(sessionId, generation, operation);
        return;
      }
      this.patch({ status: "error", draft: prompt, error });
      return;
    }
    if (!this.isCurrent(generation, operation) || abort.signal.aborted) return;
    // An owning stream_control already patched the exact replay reason and the
    // server's safe cursor; reconciling here would erase both.
    if (replayControl) return;
    await this.reconcileOwningEof(sessionId, generation, operation);
  }

  async cancel(): Promise<void> {
    const sessionId = this.requireSession();
    const generation = this.generation;
    const operation = ++this.operation;
    this.cancelAbort?.abort();
    const abort = new AbortController();
    this.cancelAbort = abort;
    try {
      const ack = await this.client.cancel(sessionId, abort.signal);
      if (!this.isCurrent(generation, operation) || abort.signal.aborted) return;
      const terminal = this.state.durableTerminal;
      if (ack.run_id !== null && terminal !== null && terminal.runId === ack.run_id) {
        this.patch({ status: "following", error: null });
        return;
      }
      this.patch({ status: "cancelling", error: null });
    } catch (error) {
      if (!this.isCurrent(generation, operation) || (abort.signal.aborted && isAbort(error))) return;
      this.patch({ status: "error", error });
    }
  }

  async resume(commandId: string, prompt: string | null = null): Promise<void> {
    const sessionId = this.requireSession();
    const terminal = this.state.durableTerminal;
    if (terminal === null || terminal.outcome === "completed") {
      throw new Error("Resume requires a durable interrupted, failed, or cancelled terminal");
    }
    if (commandId.length === 0) throw new Error("commandId must be non-empty");
    this.assertSendable("resume");
    const generation = this.generation;
    const operation = ++this.operation;
    this.owningAbort?.abort();
    const abort = new AbortController();
    this.owningAbort = abort;
    this.patch({ status: "sending", error: null });
    let admitted = false;
    let replayControl = false;
    try {
      const outcome = await this.consumeStream(
        this.client.resume(
          sessionId,
          { command_id: commandId, parent_run_id: terminal.runId, prompt },
          abort.signal,
        ),
        generation,
        operation,
        "owning",
        () => {
          admitted = true;
        },
      );
      replayControl = outcome.replayControl;
    } catch (error) {
      if (!this.isCurrent(generation, operation)) return;
      if (admitted) {
        await this.reconcileOwningEof(sessionId, generation, operation);
        return;
      }
      this.patch({ status: "error", error });
      return;
    }
    if (!this.isCurrent(generation, operation) || abort.signal.aborted) return;
    if (replayControl) return;
    await this.reconcileOwningEof(sessionId, generation, operation);
  }

  /**
   * Composer draft source of truth. Typing patches the draft directly; send()
   * keeps it until canonical admission, restores it verbatim on rejection
   * (J3), and clears it only when the user_prompt event is observed.
   */
  setDraft(draft: string): void {
    if (this.disposed) throw new Error("cannot set a draft on a disposed controller");
    this.patch({ draft });
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.generation += 1;
    this.operation += 1;
    this.abortOwnedOperations();
    this.listeners.clear();
  }

  private async loadCanonical(
    sessionId: string,
    generation: number,
    operation: number,
    abort: AbortController,
    startFollow: boolean,
  ): Promise<void> {
    try {
      let page = await this.client.snapshot(sessionId, {}, abort.signal);
      let timeline = createTimelineState();
      let followCursor = page.snapshot_cursor;
      for (;;) {
        if (!this.isGeneration(generation) || abort.signal.aborted) return;
        this.assertSnapshotSession(page, sessionId);
        for (const event of page.events) timeline = reduceChatEvent(timeline, event);
        if (page.next_cursor === null) break;
        followCursor = page.next_cursor;
        page = await this.client.snapshot(sessionId, { cursor: page.next_cursor }, abort.signal);
      }
      const live = this.state.timeline;
      for (const node of live.byId.values()) {
        timeline = reduceChatEvent(timeline, node.event);
        if (node.result !== null) timeline = reduceChatEvent(timeline, node.result);
      }
      for (const pending of live.pendingToolResults.values()) {
        timeline = reduceChatEvent(timeline, pending);
      }
      if (!this.isGeneration(generation) || abort.signal.aborted) return;
      const owning = this.isOwningStatus(this.state.status) && this.operation !== operation;
      this.state = {
        ...this.state,
        timeline,
        lastSafeCursor: followCursor,
        durableTerminal: this.findLatestTerminal(timeline),
        status: owning ? this.state.status : "following",
        replayReason: owning ? this.state.replayReason : null,
        error: owning ? this.state.error : null,
      };
      this.emit();
      if (startFollow && !owning) {
        void this.runPassiveFollow(sessionId, followCursor, generation, abort);
      }
    } catch (error) {
      if (!this.isGeneration(generation) || (abort.signal.aborted && isAbort(error))) return;
      if (this.isOwningStatus(this.state.status) && this.operation !== operation) return;
      this.patch({ status: "error", error });
    }
  }

  private async runPassiveFollow(
    sessionId: string,
    cursor: string,
    generation: number,
    abort: AbortController,
  ): Promise<void> {
    if (this.passiveFollowRunning) {
      this.pendingPassiveFollow = { sessionId, cursor, generation, abort };
      return;
    }
    this.passiveFollowRunning = true;
    this.pendingPassiveFollow = null;
    try {
      const operation = this.operation;
      let nextCursor = cursor;
      for (let attempt = 0; ; attempt += 1) {
        let failed = false;
        try {
          await this.consumeStream(
            this.client.follow(sessionId, nextCursor, abort.signal),
            generation,
            operation,
            "passive",
            attempt > 0
              ? () => {
                  if (this.state.status === "reconnecting") {
                    this.patch({ status: "following", error: null });
                  }
                }
              : undefined,
          );
        } catch (error) {
          if (!this.isGeneration(generation) || abort.signal.aborted) return;
          if (this.isOwningStatus(this.state.status)) {
            failed = true;
          } else if (error instanceof ChatApiError && error.error.replay_required === true) {
            this.patch({
              status: "replay_required",
              replayReason: error.error.code,
              error,
            });
            return;
          } else if (error instanceof ChatApiError && error.error.retryable === false) {
            this.patch({ status: "error", error });
            return;
          } else {
            failed = true;
            this.patch({ status: "reconnecting", error });
          }
        }
        if (!this.isGeneration(generation) || abort.signal.aborted || this.state.status === "replay_required") return;
        if (!failed && !this.isOwningStatus(this.state.status)) {
          this.patch({ status: "reconnecting" });
        }
        const completed = await this.sleep(this.reconnectDelayMs(attempt), abort.signal);
        if (!completed || !this.isGeneration(generation) || abort.signal.aborted) return;
        nextCursor = this.requireSafeCursor();
      }
    } finally {
      this.passiveFollowRunning = false;
      const pending = this.pendingPassiveFollow;
      this.pendingPassiveFollow = null;
      if (pending !== null && pending.generation === this.generation && !this.disposed) {
        void this.runPassiveFollow(
          pending.sessionId,
          pending.cursor,
          pending.generation,
          pending.abort,
        );
      }
    }
  }

  private async consumeStream(
    stream: AsyncIterable<ChatStreamItem>,
    generation: number,
    operation: number,
    kind: StreamKind,
    onFirstEvent?: () => void,
  ): Promise<ConsumeOutcome> {
    let observedEvent = false;
    for await (const item of stream) {
      const current = kind === "passive" ? this.isGeneration(generation) : this.isCurrent(generation, operation);
      if (!current) return { observedEvent, replayControl: false };
      if (item.type === "stream_control") {
        if (kind === "passive" && this.state.status === "sending") {
          return { observedEvent, replayControl: true };
        }
        this.patch({
          status: "replay_required",
          replayReason: item.control.reason,
          lastSafeCursor: item.control.cursor,
          error: null,
        });
        return { observedEvent, replayControl: true };
      }
      this.assertEventSession(item.event.session_id);
      if (!observedEvent) onFirstEvent?.();
      observedEvent = true;
      const timeline = reduceChatEvent(this.state.timeline, item.event);
      const durableTerminal =
        item.event.kind === "root_terminal"
          ? this.findLatestTerminal(timeline)
          : this.state.durableTerminal;
      this.patch({
        timeline,
        durableTerminal,
        ...(item.event.kind === "root_terminal" && this.state.status === "cancelling"
          ? { status: "following" as const }
          : {}),
      });
    }
    return { observedEvent, replayControl: false };
  }

  private async reconcileOwningEof(sessionId: string, generation: number, operation: number) {
    if (!this.isCurrent(generation, operation)) return;
    const abort = new AbortController();
    this.owningAbort = abort;
    const timer = setTimeout(() => abort.abort(), this.finalizeTimeoutMs);
    try {
      await this.loadCanonical(sessionId, generation, operation, abort, true);
      if (abort.signal.aborted && this.isCurrent(generation, operation) && this.state.status === "sending") {
        this.patch({
          status: "error",
          error: new Error("canonical finalization timed out"),
        });
      }
    } finally {
      clearTimeout(timer);
    }
  }

  private isOwningStatus(status: ConnectedChatStatus): boolean {
    return status === "sending" || status === "cancelling";
  }

  private findLatestTerminal(timeline: TimelineState): DurableTerminal | null {
    for (let index = timeline.order.length - 1; index >= 0; index -= 1) {
      const node = timeline.byId.get(timeline.order[index]);
      if (!node) throw new Error("timeline order references a missing node");
      if (node.event.kind === "root_terminal") {
        return this.terminalFromEvent(node.event.run_id, node.event.payload);
      }
    }
    return null;
  }

  private terminalFromEvent(runId: string | null, payload: RootTerminalPayload): DurableTerminal {
    if (runId === null) throw new Error("root_terminal must have a run_id");
    return { ...payload, runId };
  }

  private assertSnapshotSession(snapshot: ChatSnapshot, sessionId: string) {
    if (snapshot.session_id !== sessionId) {
      throw new Error(`snapshot session mismatch: expected ${sessionId}, received ${snapshot.session_id}`);
    }
  }

  private assertEventSession(sessionId: string) {
    if (sessionId !== this.state.sessionId) {
      throw new Error(`event session mismatch: expected ${this.state.sessionId}, received ${sessionId}`);
    }
  }

  private requireSession(): string {
    if (this.disposed) throw new Error("controller is disposed");
    if (this.state.sessionId === null) throw new Error("no session selected");
    return this.state.sessionId;
  }

  private assertSendable(action: "send" | "resume"): void {
    if (this.state.status === "replay_required") {
      throw new Error(`cannot ${action} while replay is required`);
    }
    if (this.state.status === "loading") {
      throw new Error(`cannot ${action} while the session is loading`);
    }
  }

  private requireSafeCursor(): string {
    if (this.state.lastSafeCursor === null) throw new Error("cannot follow without a safe cursor");
    return this.state.lastSafeCursor;
  }

  private isGeneration(generation: number): boolean {
    return !this.disposed && generation === this.generation;
  }

  private isCurrent(generation: number, operation: number): boolean {
    return this.isGeneration(generation) && operation === this.operation;
  }

  private abortOwnedOperations() {
    this.selectionAbort?.abort();
    this.owningAbort?.abort();
    this.cancelAbort?.abort();
    this.selectionAbort = null;
    this.owningAbort = null;
    this.cancelAbort = null;
  }

  private replace(state: ConnectedChatState) {
    this.state = state;
    this.emit();
  }

  private patch(patch: Partial<ConnectedChatState>) {
    this.state = { ...this.state, ...patch };
    this.emit();
  }

  private emit() {
    for (const listener of this.listeners) listener();
  }
}
