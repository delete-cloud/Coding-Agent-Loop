// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const session = (
  id: string,
  model: string,
  overrides: Record<string, unknown> = {},
) => ({
  session_id: id,
  id,
  status: "completed",
  turn_status: "idle",
  turn_id: null,
  created_at: "2026-06-12T00:00:00Z",
  updated_at: "2026-06-12T00:00:00Z",
  last_activity: "2026-06-12T00:00:00Z",
  turn_in_progress: false,
  pending_approval: false,
  provider_name: "test",
  model_name: model,
  base_url: null,
  max_steps: 12,
  origin: null,
  default_run_target: {},
  workspace_id: null,
  resumable: true,
  last_run_id: null,
  last_run_status: null,
  last_interrupted_run_id: null,
  resume_from_event_id: null,
  checkpoint_count: 0,
  latest_checkpoint_id: null,
  latest_checkpoint_label: null,
  ...overrides,
});

const jsonResponse = (body: unknown) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });

describe("App session switching", () => {
  beforeEach(() => {
    const storage = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
      clear: () => storage.clear(),
      removeItem: (key: string) => storage.delete(key),
    });
    Element.prototype.scrollIntoView = vi.fn();
    localStorage.setItem(
      "coding-agent-webui-config",
      JSON.stringify({
        baseUrl: "http://127.0.0.1:18080",
        apiKey: "",
        repoPath: "",
        approval: "auto",
      }),
    );
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("aborts an active prompt stream before loading another session", async () => {
    const first = session("session-one-0001", "model-a");
    const second = session("session-two-0002", "model-b");
    let activePromptSignal: AbortSignal | undefined;
    let promptPath = "";

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: [first, second] }));
        }
        if (url.endsWith(`/sessions/${first.session_id}`)) {
          return Promise.resolve(jsonResponse(first));
        }
        if (url.endsWith(`/sessions/${second.session_id}`)) {
          return Promise.resolve(jsonResponse(second));
        }
        if (url.endsWith(`/sessions/${first.session_id}/runs`)) {
          return Promise.resolve(jsonResponse({ session_id: first.session_id, runs: [] }));
        }
        if (url.endsWith(`/sessions/${second.session_id}/runs`)) {
          return Promise.resolve(jsonResponse({ session_id: second.session_id, runs: [] }));
        }
        if (url.includes(`/sessions/${first.session_id}/prompt?event_format=display`)) {
          activePromptSignal = init?.signal ?? undefined;
          promptPath = url;
          return Promise.resolve(
            new Response(
              new ReadableStream({
                start(controller) {
                  controller.enqueue(new TextEncoder().encode(": keep-alive\n\n"));
                },
              }),
              { status: 200, headers: { "Content-Type": "text/event-stream" } },
            ),
          );
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /model-a/i }));
    await screen.findByText("idle");
    fireEvent.change(screen.getByPlaceholderText(/ask the agent/i), {
      target: { value: "continue" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(activePromptSignal).toBeDefined());
    expect(promptPath).toContain(`/sessions/${first.session_id}/prompt?event_format=display`);
    fireEvent.click(screen.getByRole("button", { name: /model-b/i }));

    expect(activePromptSignal?.aborted).toBe(true);
  });

  it("defaults API base URL to same origin when no config is stored", async () => {
    localStorage.removeItem("coding-agent-webui-config");
    let sessionsUrl = "";

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          sessionsUrl = url;
          return Promise.resolve(jsonResponse({ sessions: [] }));
        }
        if (url.endsWith("/healthz")) {
          return Promise.resolve(jsonResponse({ status: "ok", sessions: 0, version: "0.0.0" }));
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    await waitFor(() => expect(sessionsUrl).toBe(`${window.location.origin}/sessions`));
    // The default same-origin profile is seeded and persisted.
    const stored = JSON.parse(
      localStorage.getItem("coding-agent-webui-profiles") ?? "{}",
    ) as { profiles?: Array<{ baseUrl?: string }> };
    expect(stored.profiles?.[0]?.baseUrl).toBe(window.location.origin);
  });

  it("switching the active profile swaps the backend and refreshes the session list", async () => {
    const onA = session("session-a-0001", "model-a");
    const onB = session("session-b-0002", "model-b");
    localStorage.setItem(
      "coding-agent-webui-profiles",
      JSON.stringify({
        profiles: [
          { id: "pa", name: "backend-a", baseUrl: "http://a.test", apiKey: "" },
          { id: "pb", name: "backend-b", baseUrl: "http://b.test", apiKey: "" },
        ],
        activeId: "pa",
      }),
    );
    const sessionListCalls: string[] = [];

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "http://a.test/sessions") {
          sessionListCalls.push(url);
          return Promise.resolve(jsonResponse({ sessions: [onA] }));
        }
        if (url === "http://b.test/sessions") {
          sessionListCalls.push(url);
          return Promise.resolve(jsonResponse({ sessions: [onB] }));
        }
        if (url.endsWith("/healthz")) {
          return Promise.resolve(jsonResponse({ status: "ok", sessions: 1, version: "1.0.0" }));
        }
        if (url.endsWith("/oauth/accounts")) {
          return Promise.resolve(jsonResponse([]));
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    // Backend A is active on load.
    expect(await screen.findByRole("button", { name: /model-a/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: "connection" }).textContent).toContain(
      "backend-a",
    );

    fireEvent.click(screen.getByRole("button", { name: "connection" }));
    fireEvent.click(await screen.findByRole("button", { name: "Switch" }));

    // The session list was cleared and re-fetched from backend B.
    await waitFor(() => expect(sessionListCalls).toContain("http://b.test/sessions"));
    expect(await screen.findByRole("button", { name: /model-b/i })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /model-a/i })).toBeNull();
    expect(screen.getByRole("button", { name: "connection" }).textContent).toContain(
      "backend-b",
    );
  });

  it("discards a stale session list response from the old backend after a profile switch", async () => {
    const onA = session("session-a-0001", "model-a");
    const onB = session("session-b-0002", "model-b");
    localStorage.setItem(
      "coding-agent-webui-profiles",
      JSON.stringify({
        profiles: [
          { id: "pa", name: "backend-a", baseUrl: "http://a.test", apiKey: "" },
          { id: "pb", name: "backend-b", baseUrl: "http://b.test", apiKey: "" },
        ],
        activeId: "pa",
      }),
    );
    const oldList = deferred<Response>();

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "http://a.test/sessions") {
          // Old backend is slow: its list response is still in flight when the
          // profile switch happens.
          return oldList.promise;
        }
        if (url === "http://b.test/sessions") {
          return Promise.resolve(jsonResponse({ sessions: [onB] }));
        }
        if (url.endsWith("/healthz")) {
          return Promise.resolve(jsonResponse({ status: "ok", sessions: 1, version: "1.0.0" }));
        }
        if (url.endsWith("/oauth/accounts")) {
          return Promise.resolve(jsonResponse([]));
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    // The initial list request to backend A is in flight (unresolved).
    fireEvent.click(screen.getByRole("button", { name: "connection" }));
    fireEvent.click(await screen.findByRole("button", { name: "Switch" }));

    // Backend B's list wins.
    expect(await screen.findByRole("button", { name: /model-b/i })).toBeTruthy();

    // The stale response from backend A resolves late and must be discarded.
    oldList.resolve(jsonResponse({ sessions: [onA] }));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByRole("button", { name: /model-a/i })).toBeNull();
    expect(screen.getByRole("button", { name: /model-b/i })).toBeTruthy();
  });

  it("uses prompt for completed restored sessions even when historical resume metadata remains", async () => {
    const completed = session("session-completed-0001", "model-completed", {
      resumable: true,
      last_run_id: "run-completed",
      last_run_status: "completed",
      last_interrupted_run_id: "run-old-interrupted",
      resume_from_event_id: "event-latest",
    });
    let promptPath = "";

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: [completed] }));
        }
        if (url.endsWith(`/sessions/${completed.session_id}`)) {
          return Promise.resolve(jsonResponse(completed));
        }
        if (url.endsWith(`/sessions/${completed.session_id}/runs`)) {
          return Promise.resolve(jsonResponse({ session_id: completed.session_id, runs: [] }));
        }
        if (url.includes(`/sessions/${completed.session_id}/prompt?event_format=display`)) {
          promptPath = url;
          return Promise.resolve(
            new Response(
              new ReadableStream({
                start(controller) {
                  controller.close();
                },
              }),
              { status: 200, headers: { "Content-Type": "text/event-stream" } },
            ),
          );
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /model-completed/i }));
    await screen.findByText("idle");
    fireEvent.change(screen.getByPlaceholderText(/ask the agent/i), {
      target: { value: "continue normally" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() =>
      expect(promptPath).toContain(
        `/sessions/${completed.session_id}/prompt?event_format=display`,
      ),
    );
  });

  it("disables sending while a new session is being created", async () => {
    const existing = session("session-existing-0001", "model-existing");
    const createSession = deferred<Response>();
    let postedToExisting = false;

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/sessions") && init?.method === "POST") {
          return createSession.promise;
        }
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: [existing] }));
        }
        if (url.endsWith(`/sessions/${existing.session_id}`)) {
          return Promise.resolve(jsonResponse(existing));
        }
        if (url.endsWith(`/sessions/${existing.session_id}/runs`)) {
          return Promise.resolve(jsonResponse({ session_id: existing.session_id, runs: [] }));
        }
        if (url.includes(`/sessions/${existing.session_id}/prompt?event_format=display`)) {
          postedToExisting = true;
          return Promise.resolve(new Response("", { status: 200 }));
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /model-existing/i }));
    await screen.findByText("idle");
    fireEvent.click(screen.getByRole("button", { name: "New session" }));

    expect((screen.getByRole("button", { name: "Send" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByPlaceholderText(/ask the agent/i), {
      target: { value: "should not post" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(postedToExisting).toBe(false);
  });

  it("uses resume only for restored sessions with interrupted run metadata", async () => {
    const interrupted = session("session-interrupted-0001", "model-resume", {
      status: "failed",
      last_run_status: "interrupted",
      last_interrupted_run_id: "run-interrupted",
      resume_from_event_id: "event-after",
    });
    let promptPath = "";

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: [interrupted] }));
        }
        if (url.endsWith(`/sessions/${interrupted.session_id}`)) {
          return Promise.resolve(jsonResponse(interrupted));
        }
        if (url.endsWith(`/sessions/${interrupted.session_id}/runs`)) {
          return Promise.resolve(jsonResponse({ session_id: interrupted.session_id, runs: [] }));
        }
        if (url.includes(`/sessions/${interrupted.session_id}/resume?event_format=display`)) {
          promptPath = url;
          init?.signal?.addEventListener("abort", () => undefined);
          return Promise.resolve(
            new Response(
              new ReadableStream({
                start(controller) {
                  controller.enqueue(
                    new TextEncoder().encode(
                      'event: final_result\ndata: {"source_event_id":"event-final","run_id":"run-resume","sequence":1,"display_kind":"final_result","payload":{"turn_id":"turn-1","completion_status":"completed"},"created_at":"2026-06-12T00:00:00Z"}\n\n',
                    ),
                  );
                  controller.close();
                },
              }),
              { status: 200, headers: { "Content-Type": "text/event-stream" } },
            ),
          );
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /model-resume/i }));
    await screen.findByText("idle");
    fireEvent.change(screen.getByPlaceholderText(/ask the agent/i), {
      target: { value: "resume this" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() =>
      expect(promptPath).toContain(
        `/sessions/${interrupted.session_id}/resume?event_format=display`,
      ),
    );
  });

  it("disables sending while restored session metadata is loading", async () => {
    const interrupted = session("session-loading-0001", "model-loading", {
      status: "failed",
      last_run_status: "interrupted",
      last_interrupted_run_id: "run-interrupted",
      resume_from_event_id: "event-after",
    });
    const summary = deferred<Response>();
    const runs = deferred<Response>();
    let postedPrompt = false;

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: [interrupted] }));
        }
        if (url.endsWith(`/sessions/${interrupted.session_id}`)) {
          return summary.promise;
        }
        if (url.endsWith(`/sessions/${interrupted.session_id}/runs`)) {
          return runs.promise;
        }
        if (url.includes(`/sessions/${interrupted.session_id}/prompt?event_format=display`)) {
          postedPrompt = true;
          return Promise.resolve(new Response("", { status: 200 }));
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /model-loading/i }));
    fireEvent.change(screen.getByPlaceholderText(/ask the agent/i), {
      target: { value: "too early" },
    });

    expect((screen.getByRole("button", { name: "Send" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(postedPrompt).toBe(false);

    summary.resolve(jsonResponse(interrupted));
    runs.resolve(jsonResponse({ session_id: interrupted.session_id, runs: [] }));
    await screen.findByText("idle");
  });

  it("keeps resume mode when an interrupted session resume stream closes without a final result", async () => {
    const interrupted = session("session-interrupted-0002", "model-resume", {
      status: "failed",
      last_run_status: "interrupted",
      last_interrupted_run_id: "run-interrupted",
      resume_from_event_id: "event-after",
    });
    const promptPaths: string[] = [];

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: [interrupted] }));
        }
        if (url.endsWith(`/sessions/${interrupted.session_id}`)) {
          return Promise.resolve(jsonResponse(interrupted));
        }
        if (url.endsWith(`/sessions/${interrupted.session_id}/runs`)) {
          return Promise.resolve(jsonResponse({ session_id: interrupted.session_id, runs: [] }));
        }
        if (url.includes(`/sessions/${interrupted.session_id}/resume?event_format=display`)) {
          promptPaths.push(url);
          return Promise.resolve(
            new Response(
              new ReadableStream({
                start(controller) {
                  controller.close();
                },
              }),
              { status: 200, headers: { "Content-Type": "text/event-stream" } },
            ),
          );
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /model-resume/i }));
    await screen.findByText("idle");
    fireEvent.change(screen.getByPlaceholderText(/ask the agent/i), {
      target: { value: "resume attempt one" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(promptPaths).toHaveLength(1));
    await screen.findByText("idle");
    fireEvent.change(screen.getByPlaceholderText(/ask the agent/i), {
      target: { value: "resume attempt two" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(promptPaths).toHaveLength(2));
  });

  it("ignores stale session history loads after a newer selection wins", async () => {
    const slow = session("session-slow-0001", "model-slow", {
      status: "running",
      turn_status: "running",
      turn_in_progress: true,
    });
    const fast = session("session-fast-0002", "model-fast");
    const slowSummary = deferred<Response>();
    const slowRuns = deferred<Response>();
    let followedSlow = false;

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: [slow, fast] }));
        }
        if (url.endsWith(`/sessions/${slow.session_id}`)) {
          return slowSummary.promise;
        }
        if (url.endsWith(`/sessions/${slow.session_id}/runs`)) {
          return slowRuns.promise;
        }
        if (url.endsWith(`/sessions/${fast.session_id}`)) {
          return Promise.resolve(jsonResponse(fast));
        }
        if (url.endsWith(`/sessions/${fast.session_id}/runs`)) {
          return Promise.resolve(jsonResponse({ session_id: fast.session_id, runs: [] }));
        }
        if (url.endsWith(`/sessions/${slow.session_id}/display-events`)) {
          followedSlow = true;
          return Promise.resolve(new Response("", { status: 200 }));
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /model-slow/i }));
    fireEvent.click(await screen.findByRole("button", { name: /model-fast/i }));

    await screen.findByText("idle");

    slowSummary.resolve(jsonResponse(slow));
    slowRuns.resolve(jsonResponse({ session_id: slow.session_id, runs: [] }));

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.getByText("idle")).toBeTruthy();
    expect(followedSlow).toBe(false);
  });

  it("ignores reconnect reconciliation after selecting a newer session", async () => {
    const running = session("session-running-0001", "model-running", {
      status: "running",
      turn_status: "running",
      turn_in_progress: true,
    });
    const idle = session("session-idle-0002", "model-idle");
    const runningReconcile = deferred<Response>();
    let runningSummaryCalls = 0;

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: [running, idle] }));
        }
        if (url.endsWith(`/sessions/${running.session_id}`)) {
          runningSummaryCalls += 1;
          if (runningSummaryCalls === 1) {
            return Promise.resolve(jsonResponse(running));
          }
          return runningReconcile.promise;
        }
        if (url.endsWith(`/sessions/${running.session_id}/runs`)) {
          return Promise.resolve(jsonResponse({ session_id: running.session_id, runs: [] }));
        }
        if (url.endsWith(`/sessions/${running.session_id}/display-events`)) {
          return Promise.resolve(
            new Response(
              new ReadableStream({
                start(controller) {
                  controller.close();
                },
              }),
              { status: 200, headers: { "Content-Type": "text/event-stream" } },
            ),
          );
        }
        if (url.endsWith(`/sessions/${idle.session_id}`)) {
          return Promise.resolve(jsonResponse(idle));
        }
        if (url.endsWith(`/sessions/${idle.session_id}/runs`)) {
          return Promise.resolve(jsonResponse({ session_id: idle.session_id, runs: [] }));
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /model-running/i }));
    await screen.findByText("reconnected");
    fireEvent.click(screen.getByRole("button", { name: /model-idle/i }));
    await screen.findByText("idle");

    runningReconcile.resolve(jsonResponse(running));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.getByText("idle")).toBeTruthy();
  });

  it("updates status from live reconnect progress events", async () => {
    const running = session("session-running-0001", "model-live", {
      status: "running",
      turn_status: "running",
      turn_in_progress: true,
      last_run_id: "run-live",
    });

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: [running] }));
        }
        if (url.endsWith(`/sessions/${running.session_id}`)) {
          return Promise.resolve(jsonResponse(running));
        }
        if (url.endsWith(`/sessions/${running.session_id}/runs`)) {
          return Promise.resolve(jsonResponse({ session_id: running.session_id, runs: [] }));
        }
        if (url.endsWith(`/sessions/${running.session_id}/display-events`)) {
          const payload = {
            source_event_id: "event-progress",
            run_id: "run-live",
            sequence: 1,
            display_kind: "progress_update",
            payload: {
              phase: "streaming",
              model_name: "reconnect-model",
              tokens_in: 12,
              tokens_out: 3,
              context_percent: 42,
              elapsed_seconds: 1.5,
            },
            created_at: "2026-06-12T00:00:00Z",
          };
          return Promise.resolve(
            new Response(
              new ReadableStream({
                start(controller) {
                  controller.enqueue(
                    new TextEncoder().encode(
                      `event: progress_update\ndata: ${JSON.stringify(payload)}\n\n`,
                    ),
                  );
                },
              }),
              { status: 200, headers: { "Content-Type": "text/event-stream" } },
            ),
          );
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /model-live/i }));

    expect(await screen.findByText(/streaming · reconnect-model/)).toBeTruthy();
  });

  it("posts approval decisions from streamed approval prompts", async () => {
    const active = session("session-approval-0001", "model-approval");
    let approveBody: Record<string, unknown> | null = null;

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          return jsonResponse({ sessions: [active] });
        }
        if (url.endsWith(`/sessions/${active.session_id}`)) {
          return jsonResponse(active);
        }
        if (url.endsWith(`/sessions/${active.session_id}/runs`)) {
          return jsonResponse({ session_id: active.session_id, runs: [] });
        }
        if (url.includes(`/sessions/${active.session_id}/prompt?event_format=display`)) {
          const payload = {
            source_event_id: "event-approval",
            run_id: "run-approval",
            sequence: 1,
            display_kind: "approval_prompt",
            payload: {
              agent_id: "",
              request_id: "approval-1",
              timeout_seconds: 60,
              tool_call: {
                call_id: "call-1",
                tool_name: "bash",
                arguments: { cmd: "pwd" },
              },
            },
            // Live-streamed prompt: created_at must be "now" so the approval
            // countdown deadline (created_at + timeout_seconds) is not already
            // expired when the card mounts.
            created_at: new Date().toISOString(),
          };
          return new Response(
            new ReadableStream({
              start(controller) {
                controller.enqueue(
                  new TextEncoder().encode(
                    `event: approval_prompt\ndata: ${JSON.stringify(payload)}\n\n`,
                  ),
                );
                controller.close();
              },
            }),
            { status: 200, headers: { "Content-Type": "text/event-stream" } },
          );
        }
        if (url.endsWith(`/sessions/${active.session_id}/approve`)) {
          approveBody = JSON.parse(String(init?.body)) as Record<string, unknown>;
          return jsonResponse({
            status: "approved",
            request_id: "approval-1",
            decision: "approved",
          });
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /model-approval/i }));
    await screen.findByText("idle");
    fireEvent.change(screen.getByPlaceholderText(/ask the agent/i), {
      target: { value: "run a tool" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Approval Required")).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText(/feedback/i), {
      target: { value: "looks safe" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() =>
      expect(approveBody).toEqual({
        request_id: "approval-1",
        approved: true,
        feedback: "looks safe",
        scope: "once",
      }),
    );
    expect(screen.getByText("→ approved")).toBeTruthy();
  });

  it("sends provider and model from the header config when creating a session", async () => {
    let createBody: Record<string, unknown> | null = null;

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/sessions") && init?.method === "POST") {
          createBody = JSON.parse(String(init.body)) as Record<string, unknown>;
          return Promise.resolve(jsonResponse({ session_id: "session-new-0001" }));
        }
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: [] }));
        }
        if (url.endsWith("/sessions/session-new-0001/runs")) {
          return Promise.resolve(jsonResponse({ session_id: "session-new-0001", runs: [] }));
        }
        if (url.includes("/sessions/session-new-0001/memory/reviews")) {
          return Promise.resolve(jsonResponse([]));
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: "New session" }));

    await waitFor(() => expect(createBody).not.toBeNull());
    expect(createBody).toMatchObject({
      approval_policy: "auto",
      provider: "kimi-code",
      model: "kimi-for-coding",
    });
  });

  it("omits provider and model when server default is selected", async () => {
    let createBody: Record<string, unknown> | null = null;

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/sessions") && init?.method === "POST") {
          createBody = JSON.parse(String(init.body)) as Record<string, unknown>;
          return Promise.resolve(jsonResponse({ session_id: "session-new-0002" }));
        }
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: [] }));
        }
        if (url.endsWith("/sessions/session-new-0002/runs")) {
          return Promise.resolve(jsonResponse({ session_id: "session-new-0002", runs: [] }));
        }
        if (url.includes("/sessions/session-new-0002/memory/reviews")) {
          return Promise.resolve(jsonResponse([]));
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    fireEvent.change(screen.getByTitle("provider"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "New session" }));

    await waitFor(() => expect(createBody).not.toBeNull());
    expect(createBody).not.toHaveProperty("provider");
    expect(createBody).not.toHaveProperty("model");
  });

  it("deletes a session after confirmation and removes it from the list", async () => {
    const doomed = session("session-doomed-0001", "model-doomed");
    let deletePath = "";
    let deleteMethod = "";
    let deleted = false;
    vi.stubGlobal("confirm", vi.fn(() => true));

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith(`/sessions/${doomed.session_id}`) && init?.method === "DELETE") {
          deletePath = url;
          deleteMethod = init.method;
          deleted = true;
          return Promise.resolve(
            jsonResponse({ status: "closed", session_id: doomed.session_id }),
          );
        }
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: deleted ? [] : [doomed] }));
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    expect(await screen.findByRole("button", { name: /model-doomed/i })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /close session/i }));

    await waitFor(() => expect(deletePath).not.toBe(""));
    expect(deleteMethod).toBe("DELETE");
    expect(deletePath).toContain(`/sessions/${doomed.session_id}`);
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /model-doomed/i })).toBeNull(),
    );
  });

  it("surfaces a readable error and keeps the session when delete returns 409", async () => {
    const stuck = session("session-stuck-0001", "model-stuck");
    vi.stubGlobal("confirm", vi.fn(() => true));

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: [stuck] }));
        }
        if (url.endsWith(`/sessions/${stuck.session_id}`) && init?.method === "DELETE") {
          return Promise.resolve(
            new Response("session turn is owned elsewhere", { status: 409 }),
          );
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    expect(await screen.findByRole("button", { name: /model-stuck/i })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /close session/i }));

    expect(
      await screen.findByText(/close session failed: 409 session turn is owned elsewhere/i),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: /model-stuck/i })).toBeTruthy();
  });

  it("applies only the latest refreshMemory response when same-session refreshes resolve out of order", async () => {
    const active = session("session-memory-0001", "model-memory");
    const memory = (id: string, title: string) => ({
      candidate_id: id,
      status: "accepted",
      kind: "fact",
      title,
      summary: `summary for ${title}`,
      scope: "project",
      tags: [],
      confidence: 0.9,
    });
    const refreshRuns: Array<ReturnType<typeof deferred<Response>>> = [];
    const refreshReviews: Array<ReturnType<typeof deferred<Response>>> = [];
    let runsCalls = 0;

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: [active] }));
        }
        if (url.endsWith(`/sessions/${active.session_id}`)) {
          return Promise.resolve(jsonResponse(active));
        }
        if (url.endsWith(`/sessions/${active.session_id}/runs`)) {
          runsCalls += 1;
          // First runs call comes from displayEvents during loadSession.
          if (runsCalls === 1) {
            return Promise.resolve(jsonResponse({ session_id: active.session_id, runs: [] }));
          }
          const d = deferred<Response>();
          refreshRuns.push(d);
          return d.promise;
        }
        if (url.includes(`/sessions/${active.session_id}/memory/reviews`)) {
          const d = deferred<Response>();
          refreshReviews.push(d);
          return d.promise;
        }
        if (url.includes(`/sessions/${active.session_id}/prompt?event_format=display`)) {
          return Promise.resolve(
            new Response(
              new ReadableStream({
                start(controller) {
                  controller.close();
                },
              }),
              { status: 200, headers: { "Content-Type": "text/event-stream" } },
            ),
          );
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /model-memory/i }));
    await screen.findByText("idle");
    // The memory panel lives in the right rail now; open it to observe state.
    fireEvent.click(screen.getByRole("button", { name: "Toggle memory panel" }));
    // loadSession's refreshMemory (seq 1) is now in flight.
    await waitFor(() => expect(refreshRuns).toHaveLength(1));

    // Trigger a second refreshMemory (seq 2) via send()'s finally block.
    fireEvent.change(screen.getByPlaceholderText(/ask the agent/i), {
      target: { value: "hi" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(refreshRuns).toHaveLength(2));
    await waitFor(() => expect(refreshReviews).toHaveLength(2));

    // The newer refresh resolves first and applies.
    refreshRuns[1].resolve(jsonResponse({ session_id: active.session_id, runs: [] }));
    refreshReviews[1].resolve(jsonResponse([memory("mem-new", "latest-memory")]));
    expect(await screen.findByText("latest-memory")).toBeTruthy();

    // The older refresh resolves later and must not clobber the newer state.
    refreshRuns[0].resolve(jsonResponse({ session_id: active.session_id, runs: [] }));
    refreshReviews[0].resolve(jsonResponse([memory("mem-old", "stale-memory")]));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.getByText("latest-memory")).toBeTruthy();
    expect(screen.queryByText("stale-memory")).toBeNull();
  });

  it("shows a memory error instead of loading forever when session restore fails", async () => {
    const broken = session("session-broken-0001", "model-broken");

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: [broken] }));
        }
        if (url.endsWith(`/sessions/${broken.session_id}`)) {
          return Promise.resolve(new Response("boom", { status: 500 }));
        }
        if (url.endsWith(`/sessions/${broken.session_id}/runs`)) {
          return Promise.resolve(new Response("boom", { status: 500 }));
        }
        if (url.includes(`/sessions/${broken.session_id}/memory/reviews`)) {
          return Promise.resolve(new Response("boom", { status: 500 }));
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /model-broken/i }));
    fireEvent.click(screen.getByRole("button", { name: "Toggle memory panel" }));

    expect(await screen.findByText("restore failed")).toBeTruthy();
    expect(await screen.findByText(/memory load failed/)).toBeTruthy();
    expect(screen.queryByText("Loading memory…")).toBeNull();
  });

  it("re-fetches the session list after a delete so a ghost entry cannot reappear", async () => {
    const doomed = session("session-ghost-0001", "model-ghost");
    vi.stubGlobal("confirm", vi.fn(() => true));
    const deleteDone = deferred<Response>();
    const listCalls: Array<ReturnType<typeof deferred<Response>>> = [];

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith(`/sessions/${doomed.session_id}`) && init?.method === "DELETE") {
          return deleteDone.promise;
        }
        if (url.endsWith("/sessions")) {
          const d = deferred<Response>();
          listCalls.push(d);
          return d.promise;
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    listCalls[0].resolve(jsonResponse({ sessions: [doomed] }));
    expect(await screen.findByRole("button", { name: /model-ghost/i })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /close session/i }));
    await waitFor(() => expect(listCalls.length).toBeGreaterThanOrEqual(1));

    // An in-flight list refresh issued before the DELETE resolved.
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(listCalls).toHaveLength(2));

    deleteDone.resolve(jsonResponse({ status: "closed", session_id: doomed.session_id }));

    // The delete triggers a fresh list fetch issued after the DELETE resolved.
    await waitFor(() => expect(listCalls).toHaveLength(3));

    // Stale in-flight response (still contains the ghost) lands first...
    listCalls[1].resolve(jsonResponse({ sessions: [doomed] }));
    // ...then the authoritative post-delete response lands last.
    listCalls[2].resolve(jsonResponse({ sessions: [] }));

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /model-ghost/i })).toBeNull(),
    );
  });
});

function datalistValues(): string[] {
  const list = document.getElementById("model-options");
  if (!list) return [];
  return Array.from(list.querySelectorAll("option")).map(
    (o) => o.getAttribute("value") ?? "",
  );
}

describe("theme toggle", () => {
  beforeEach(() => {
    const storage = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
      clear: () => storage.clear(),
      removeItem: (key: string) => storage.delete(key),
    });
    Element.prototype.scrollIntoView = vi.fn();
    localStorage.setItem(
      "coding-agent-webui-config",
      JSON.stringify({ baseUrl: "http://127.0.0.1:18080", apiKey: "" }),
    );

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: [] }));
        }
        if (url.includes("/providers/")) {
          return Promise.resolve(
            jsonResponse({ provider: "kimi-code", models: [], source: "unavailable" }),
          );
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
    vi.restoreAllMocks();
    delete document.documentElement.dataset.theme;
  });

  it("defaults to dark and toggles data-theme with persistence", async () => {
    render(<App />);

    expect(document.documentElement.dataset.theme).toBe("dark");

    fireEvent.click(screen.getByRole("button", { name: "toggle theme" }));
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(localStorage.getItem("coding-agent-webui-theme")).toBe("light");

    fireEvent.click(screen.getByRole("button", { name: "toggle theme" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("coding-agent-webui-theme")).toBe("dark");
  });

  it("restores a persisted light theme on load", async () => {
    localStorage.setItem("coding-agent-webui-theme", "light");

    render(<App />);

    expect(document.documentElement.dataset.theme).toBe("light");
  });
});

describe("provider model list", () => {
  beforeEach(() => {
    const storage = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
      clear: () => storage.clear(),
      removeItem: (key: string) => storage.delete(key),
    });
    Element.prototype.scrollIntoView = vi.fn();
    localStorage.setItem(
      "coding-agent-webui-config",
      JSON.stringify({ baseUrl: "http://127.0.0.1:18080", apiKey: "" }),
    );
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
    vi.restoreAllMocks();
    delete document.documentElement.dataset.theme;
  });

  it("uses live model ids in the datalist when the fetch succeeds", async () => {
    let modelsPath = "";

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: [] }));
        }
        if (url.endsWith("/providers/kimi-code/models")) {
          modelsPath = url;
          return Promise.resolve(
            jsonResponse({
              provider: "kimi-code",
              models: [{ id: "k-live-1" }, { id: "k-live-2" }],
              source: "live",
            }),
          );
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    expect(datalistValues()).toEqual(["kimi-for-coding", "k3", "deepseek-chat"]);
    await waitFor(() => expect(modelsPath).not.toBe(""));
    expect(modelsPath).toBe("http://127.0.0.1:18080/providers/kimi-code/models");
    await waitFor(() => expect(datalistValues()).toEqual(["k-live-1", "k-live-2"]));
    // The model input stays free-text (datalist, not a select).
    expect(screen.getByTitle("model").tagName).toBe("INPUT");
  });

  it("keeps the preset datalist when the fetch fails", async () => {
    let modelsRequested = false;

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: [] }));
        }
        if (url.endsWith("/providers/kimi-code/models")) {
          modelsRequested = true;
          return Promise.resolve(new Response("boom", { status: 500 }));
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    await waitFor(() => expect(modelsRequested).toBe(true));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(datalistValues()).toEqual(["kimi-for-coding", "k3", "deepseek-chat"]);
  });

  it("keeps the preset datalist when the server reports source unavailable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: [] }));
        }
        if (url.endsWith("/providers/kimi-code/models")) {
          return Promise.resolve(
            jsonResponse({ provider: "kimi-code", models: [], source: "unavailable" }),
          );
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    await waitFor(() =>
      expect(datalistValues()).toEqual(["kimi-for-coding", "k3", "deepseek-chat"]),
    );
    // Wait past the debounce + fetch; an unavailable source must not clobber the presets.
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(datalistValues()).toEqual(["kimi-for-coding", "k3", "deepseek-chat"]);
  });

  it("does not fetch models when server default provider is selected", async () => {
    let modelsRequested = false;

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: [] }));
        }
        if (url.includes("/providers/")) {
          modelsRequested = true;
          return Promise.resolve(
            jsonResponse({ provider: "kimi-code", models: [], source: "unavailable" }),
          );
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    fireEvent.change(screen.getByTitle("provider"), { target: { value: "" } });
    // Wait past the debounce window; no models request may be issued.
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(modelsRequested).toBe(false);
    expect(datalistValues()).toEqual(["kimi-for-coding", "k3", "deepseek-chat"]);
  });
});

describe("right rail", () => {
  beforeEach(() => {
    const storage = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
      clear: () => storage.clear(),
      removeItem: (key: string) => storage.delete(key),
    });
    Element.prototype.scrollIntoView = vi.fn();
    localStorage.setItem(
      "coding-agent-webui-config",
      JSON.stringify({ baseUrl: "http://127.0.0.1:18080", apiKey: "" }),
    );
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
    vi.restoreAllMocks();
  });

  const stubRailFetch = (active: ReturnType<typeof session>) => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: [active] }));
        }
        if (url.endsWith(`/sessions/${active.session_id}`)) {
          return Promise.resolve(jsonResponse(active));
        }
        if (url.endsWith(`/sessions/${active.session_id}/runs`)) {
          return Promise.resolve(jsonResponse({ session_id: active.session_id, runs: [] }));
        }
        if (url.includes(`/sessions/${active.session_id}/memory/reviews`)) {
          return Promise.resolve(jsonResponse([]));
        }
        if (url.endsWith(`/sessions/${active.session_id}/workspace/diff`)) {
          return Promise.resolve(
            jsonResponse({ session_id: active.session_id, files: [], additions: 0, deletions: 0 }),
          );
        }
        if (url.endsWith(`/sessions/${active.session_id}/workspace/patch`)) {
          return Promise.resolve(
            jsonResponse({ session_id: active.session_id, format: "unified_diff", patch: "" }),
          );
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );
  };

  it("opens, switches, and collapses rail panels", async () => {
    const active = session("session-rail-0001", "model-rail");
    stubRailFetch(active);

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /model-rail/i }));
    await screen.findByText("idle");

    const memoryToggle = screen.getByRole("button", { name: "Toggle memory panel" });
    const diffToggle = screen.getByRole("button", { name: "Toggle diff panel" });

    // Open memory panel.
    fireEvent.click(memoryToggle);
    expect(await screen.findByText("Recall hits")).toBeTruthy();
    expect(memoryToggle.getAttribute("aria-pressed")).toBe("true");

    // Switch to diff: memory closes, diff opens (fetched on demand).
    fireEvent.click(diffToggle);
    expect(await screen.findByText(/Workspace diff/)).toBeTruthy();
    expect(screen.queryByText("Recall hits")).toBeNull();
    expect(diffToggle.getAttribute("aria-pressed")).toBe("true");
    expect(memoryToggle.getAttribute("aria-pressed")).toBe("false");

    // Clicking the active icon collapses the panel.
    fireEvent.click(diffToggle);
    expect(screen.queryByText(/Workspace diff/)).toBeNull();
    expect(diffToggle.getAttribute("aria-pressed")).toBe("false");
  });

  it("shows a placeholder instead of panels when there is no active session", async () => {
    const active = session("session-rail-0002", "model-rail");
    stubRailFetch(active);

    render(<App />);
    await screen.findByRole("button", { name: /model-rail/i });

    fireEvent.click(screen.getByRole("button", { name: "Toggle memory panel" }));
    expect(await screen.findByText("No active session")).toBeTruthy();
  });

  it("collapses and restores the sidebar via the header toggle", async () => {
    const active = session("session-rail-0003", "model-rail");
    stubRailFetch(active);

    render(<App />);
    await screen.findByRole("button", { name: /model-rail/i });

    const sidebar = screen.getByLabelText("Sessions");
    expect(sidebar.className).not.toContain("hidden");

    fireEvent.click(screen.getByRole("button", { name: "toggle sidebar" }));
    expect(sidebar.className).toContain("hidden");

    fireEvent.click(screen.getByRole("button", { name: "toggle sidebar" }));
    expect(sidebar.className).not.toContain("hidden");
  });

  it("starts with the sidebar closed on small screens", async () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({
        matches: true,
        media: "(max-width: 767px)",
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    );
    const active = session("session-rail-0004", "model-rail");
    stubRailFetch(active);

    render(<App />);
    await screen.findByRole("button", { name: /model-rail/i });

    expect(screen.getByLabelText("Sessions").className).toContain("hidden");
  });
});

describe("session result refresh", () => {
  beforeEach(() => {
    const storage = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
      clear: () => storage.clear(),
      removeItem: (key: string) => storage.delete(key),
    });
    Element.prototype.scrollIntoView = vi.fn();
    localStorage.setItem(
      "coding-agent-webui-config",
      JSON.stringify({ baseUrl: "http://127.0.0.1:18080", apiKey: "" }),
    );
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("retries an empty terminal result after the prompt stream closes", async () => {
    const active = session("session-result-retry-0001", "model-result");
    let resultCalls = 0;

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: [active] }));
        }
        if (url.endsWith(`/sessions/${active.session_id}`)) {
          return Promise.resolve(jsonResponse(active));
        }
        if (url.endsWith(`/sessions/${active.session_id}/runs`)) {
          return Promise.resolve(jsonResponse({ session_id: active.session_id, runs: [] }));
        }
        if (url.includes(`/sessions/${active.session_id}/memory/reviews`)) {
          return Promise.resolve(jsonResponse([]));
        }
        if (url.endsWith(`/sessions/${active.session_id}/result`)) {
          resultCalls += 1;
          const visible = resultCalls >= 3;
          return Promise.resolve(
            jsonResponse({
              session_id: active.session_id,
              status: "failed",
              turn_status: "failed",
              turn_id: "turn-result",
              workspace_id: null,
              origin: null,
              provider_name: "test",
              model_name: active.model_name,
              final_answer: null,
              verification_summary: null,
              failure_details: visible ? "delayed failure details" : null,
            }),
          );
        }
        if (url.includes(`/sessions/${active.session_id}/prompt?event_format=display`)) {
          return Promise.resolve(new Response("", { status: 200 }));
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /model-result/i }));
    await screen.findByText("idle");
    fireEvent.change(screen.getByPlaceholderText(/ask the agent/i), {
      target: { value: "run" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("delayed failure details")).toBeTruthy();
    expect(resultCalls).toBe(3);
  });
});


describe("thinking toggle", () => {
  beforeEach(() => {
    const storage = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
      clear: () => storage.clear(),
      removeItem: (key: string) => storage.delete(key),
    });
    Element.prototype.scrollIntoView = vi.fn();
    localStorage.setItem(
      "coding-agent-webui-config",
      JSON.stringify({ baseUrl: "http://127.0.0.1:18080", apiKey: "" }),
    );

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: [] }));
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("defaults to showing thinking and persists the toggle", async () => {
    render(<App />);

    const btn = screen.getByRole("button", { name: "toggle thinking" });
    expect(btn.getAttribute("aria-pressed")).toBe("true");

    fireEvent.click(btn);
    expect(btn.getAttribute("aria-pressed")).toBe("false");
    expect(localStorage.getItem("coding-agent-webui-show-thinking")).toBe("0");

    fireEvent.click(btn);
    expect(btn.getAttribute("aria-pressed")).toBe("true");
    expect(localStorage.getItem("coding-agent-webui-show-thinking")).toBe("1");
  });
});

describe("approval scope", () => {
  beforeEach(() => {
    const storage = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
      clear: () => storage.clear(),
      removeItem: (key: string) => storage.delete(key),
    });
    Element.prototype.scrollIntoView = vi.fn();
    localStorage.setItem(
      "coding-agent-webui-config",
      JSON.stringify({ baseUrl: "http://127.0.0.1:18080", apiKey: "", approval: "auto" }),
    );
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("sends scope=session when choosing always allow", async () => {
    const active = session("session-approval-0002", "model-approval");
    let approveBody: Record<string, unknown> | null = null;

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          return jsonResponse({ sessions: [active] });
        }
        if (url.endsWith(`/sessions/${active.session_id}`)) {
          return jsonResponse(active);
        }
        if (url.endsWith(`/sessions/${active.session_id}/runs`)) {
          return jsonResponse({ session_id: active.session_id, runs: [] });
        }
        if (url.includes(`/sessions/${active.session_id}/prompt?event_format=display`)) {
          const payload = {
            source_event_id: "event-approval",
            run_id: "run-approval",
            sequence: 1,
            display_kind: "approval_prompt",
            payload: {
              agent_id: "",
              request_id: "approval-9",
              timeout_seconds: 60,
              tool_call: {
                call_id: "call-1",
                tool_name: "bash",
                arguments: { cmd: "pwd" },
              },
            },
            // Live-streamed prompt: created_at must be "now" so the approval
            // countdown deadline (created_at + timeout_seconds) is not already
            // expired when the card mounts.
            created_at: new Date().toISOString(),
          };
          return new Response(
            new ReadableStream({
              start(controller) {
                controller.enqueue(
                  new TextEncoder().encode(
                    `event: approval_prompt\ndata: ${JSON.stringify(payload)}\n\n`,
                  ),
                );
                controller.close();
              },
            }),
            { status: 200, headers: { "Content-Type": "text/event-stream" } },
          );
        }
        if (url.endsWith(`/sessions/${active.session_id}/approve`)) {
          approveBody = JSON.parse(String(init?.body)) as Record<string, unknown>;
          return jsonResponse({
            status: "approved",
            request_id: "approval-9",
            decision: "approved",
          });
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /model-approval/i }));
    await screen.findByText("idle");
    fireEvent.change(screen.getByPlaceholderText(/ask the agent/i), {
      target: { value: "run a tool" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Approval Required")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Always allow (this session)" }));

    await waitFor(() =>
      expect(approveBody).toEqual({
        request_id: "approval-9",
        approved: true,
        feedback: null,
        scope: "session",
      }),
    );
    expect(screen.getByText("→ approved")).toBeTruthy();
  });
});

describe("session settings panel", () => {
  beforeEach(() => {
    const storage = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
      clear: () => storage.clear(),
      removeItem: (key: string) => storage.delete(key),
    });
    Element.prototype.scrollIntoView = vi.fn();
    localStorage.setItem(
      "coding-agent-webui-config",
      JSON.stringify({ baseUrl: "http://127.0.0.1:18080", apiKey: "", approval: "auto" }),
    );
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("patches runtime config when session settings change", async () => {
    const active = session("session-settings-0001", "model-settings");
    const patches: Array<{ method: string | undefined; body: Record<string, unknown> }> = [];

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: [active] }));
        }
        if (url.endsWith(`/sessions/${active.session_id}/runtime-config`)) {
          const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
          patches.push({
            method: init?.method,
            body,
          });
          return Promise.resolve(
            jsonResponse({
              session_id: active.session_id,
              provider_name: (body.provider as string | undefined) ?? active.provider_name,
              model_name: (body.model as string | undefined) ?? active.model_name,
              base_url: null,
            }),
          );
        }
        if (url.endsWith(`/sessions/${active.session_id}`)) {
          return Promise.resolve(jsonResponse(active));
        }
        if (url.endsWith(`/sessions/${active.session_id}/runs`)) {
          return Promise.resolve(jsonResponse({ session_id: active.session_id, runs: [] }));
        }
        if (url.includes(`/sessions/${active.session_id}/memory/reviews`)) {
          return Promise.resolve(jsonResponse([]));
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /model-settings/i }));
    await screen.findByText("idle");
    fireEvent.click(screen.getByRole("button", { name: "Toggle settings panel" }));
    expect(await screen.findByText("Session settings")).toBeTruthy();

    fireEvent.change(screen.getByTitle("session approval policy"), {
      target: { value: "yolo" },
    });
    await waitFor(() =>
      expect(patches).toContainEqual({ method: "PATCH", body: { approval: "yolo" } }),
    );
    // Approval changes also update the persisted new-session default.
    expect(
      (JSON.parse(localStorage.getItem("coding-agent-webui-config") ?? "{}") as { approval?: string })
        .approval,
    ).toBe("yolo");

    fireEvent.change(screen.getByTitle("thinking effort"), {
      target: { value: "high" },
    });
    await waitFor(() =>
      expect(patches).toContainEqual({
        method: "PATCH",
        body: { thinking: { enabled: true, effort: "high" } },
      }),
    );

    fireEvent.change(screen.getByTitle("session model"), {
      target: { value: "model-override" },
    });
    fireEvent.blur(screen.getByTitle("session model"));
    await waitFor(() =>
      expect(patches).toContainEqual({ method: "PATCH", body: { model: "model-override" } }),
    );

    expect(await screen.findByText("Saved")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Toggle settings panel" }));
    fireEvent.click(screen.getByRole("button", { name: "Toggle settings panel" }));
    expect((await screen.findByTitle("session model") as HTMLInputElement).value).toBe(
      "model-override",
    );
  });

  it("keeps queued settings updates bound to their originating session", async () => {
    const first = session("session-settings-0002", "model-settings-a");
    const second = session("session-settings-0003", "model-settings-b");
    const firstPatch = deferred<Response>();
    const patchUrls: string[] = [];

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: [first, second] }));
        }
        if (url.endsWith(`/sessions/${first.session_id}/runtime-config`)) {
          patchUrls.push(url);
          if (patchUrls.length === 1) return firstPatch.promise;
          return Promise.resolve(
            jsonResponse({
              session_id: first.session_id,
              provider_name: first.provider_name,
              model_name: first.model_name,
              base_url: null,
            }),
          );
        }
        if (url.endsWith(`/sessions/${second.session_id}/runtime-config`)) {
          patchUrls.push(url);
          return Promise.resolve(
            jsonResponse({
              session_id: second.session_id,
              provider_name: second.provider_name,
              model_name: second.model_name,
              base_url: null,
            }),
          );
        }
        if (url.endsWith(`/sessions/${first.session_id}`)) {
          return Promise.resolve(jsonResponse(first));
        }
        if (url.endsWith(`/sessions/${second.session_id}`)) {
          return Promise.resolve(jsonResponse(second));
        }
        if (url.endsWith(`/sessions/${first.session_id}/runs`)) {
          return Promise.resolve(jsonResponse({ session_id: first.session_id, runs: [] }));
        }
        if (url.endsWith(`/sessions/${second.session_id}/runs`)) {
          return Promise.resolve(jsonResponse({ session_id: second.session_id, runs: [] }));
        }
        if (url.includes(`/sessions/${first.session_id}/memory/reviews`)) {
          return Promise.resolve(jsonResponse([]));
        }
        if (url.includes(`/sessions/${second.session_id}/memory/reviews`)) {
          return Promise.resolve(jsonResponse([]));
        }
        throw new Error(`unexpected fetch ${url} ${init?.method ?? "GET"}`);
      }),
    );

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /model-settings-a/i }));
    await screen.findByText("idle");
    fireEvent.click(screen.getByRole("button", { name: "Toggle settings panel" }));
    const approval = await screen.findByTitle("session approval policy");

    fireEvent.change(approval, { target: { value: "yolo" } });
    await waitFor(() => expect(patchUrls).toHaveLength(1));
    fireEvent.change(approval, { target: { value: "interactive" } });

    fireEvent.click(screen.getByRole("button", { name: /model-settings-b/i }));
    await screen.findByText("idle");

    firstPatch.resolve(
      jsonResponse({
        session_id: first.session_id,
        provider_name: first.provider_name,
        model_name: first.model_name,
        base_url: null,
      }),
    );

    await waitFor(() => expect(patchUrls).toHaveLength(2));
    expect(patchUrls).toEqual([
      expect.stringContaining(`/sessions/${first.session_id}/runtime-config`),
      expect.stringContaining(`/sessions/${first.session_id}/runtime-config`),
    ]);
  });

  it("shows the codex accounts card when there is no active session", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: [] }));
        }
        if (url.endsWith("/oauth/accounts") || url.endsWith("/oauth/codex/flows")) {
          return Promise.resolve(jsonResponse([]));
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: "Toggle settings panel" }));
    expect(
      await screen.findByText(/Select a session to edit its runtime settings/),
    ).toBeTruthy();
  });
});

describe("P2 race guards", () => {
  beforeEach(() => {
    const storage = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
      clear: () => storage.clear(),
      removeItem: (key: string) => storage.delete(key),
    });
    Element.prototype.scrollIntoView = vi.fn();
    localStorage.setItem(
      "coding-agent-webui-config",
      JSON.stringify({
        baseUrl: "http://127.0.0.1:18080",
        apiKey: "",
        repoPath: "",
        approval: "auto",
      }),
    );
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("rebuilds messages from the active run list after checkpoint restore", async () => {
    const active = session("session-restore-active-0001", "model-restore", {
      checkpoint_count: 1,
    });
    const checkpoint = {
      checkpoint_id: "cp-active",
      tape_id: "tape-active",
      session_id: active.session_id,
      entry_count: 1,
      window_start: 0,
      created_at: "2026-06-12T00:00:00Z",
      label: "active-boundary",
    };
    let runsCalls = 0;

    const displayEvent = (runId: string, content: string) => ({
      source_event_id: `${runId}-event`,
      run_id: runId,
      sequence: 1,
      display_kind: "assistant_text_delta",
      payload: { content, role: "assistant" },
      created_at: "2026-06-12T00:01:00Z",
    });

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: [active] }));
        }
        if (url.endsWith(`/sessions/${active.session_id}/checkpoints`)) {
          return Promise.resolve(jsonResponse({ checkpoints: [checkpoint] }));
        }
        if (url.endsWith(`/sessions/${active.session_id}/checkpoints/cp-active/restore`)) {
          return Promise.resolve(jsonResponse({}));
        }
        if (url.endsWith(`/sessions/${active.session_id}`)) {
          return Promise.resolve(jsonResponse(active));
        }
        if (url.endsWith(`/sessions/${active.session_id}/runs`)) {
          runsCalls += 1;
          return Promise.resolve(
            jsonResponse({
              session_id: active.session_id,
              runs: runsCalls === 1
                ? [{ run_id: "run-active" }, { run_id: "run-rolled-back" }]
                : [{ run_id: "run-active" }],
            }),
          );
        }
        if (url.includes("/runs/run-active/display-events?")) {
          return Promise.resolve(
            jsonResponse({
              run_id: "run-active",
              events: [displayEvent("run-active", "Restored answer")],
            }),
          );
        }
        if (url.includes("/runs/run-rolled-back/display-events?")) {
          return Promise.resolve(
            jsonResponse({
              run_id: "run-rolled-back",
              events: [displayEvent("run-rolled-back", "Rolled back answer")],
            }),
          );
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /model-restore/i }));
    expect(await screen.findByText("Rolled back answer")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Toggle checkpoints panel" }));
    fireEvent.click(await screen.findByRole("button", { name: "Restore" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm restore" }));

    await waitFor(() => expect(runsCalls).toBe(2));
    await waitFor(() => expect(screen.queryByText("Rolled back answer")).toBeNull());
    expect(screen.getByText("Restored answer")).toBeTruthy();
  });

  it("applies only the active session's diff when fetches resolve out of order", async () => {
    const first = session("session-diff-0001", "model-diff-a");
    const second = session("session-diff-0002", "model-diff-b");
    const diffA = deferred<Response>();
    const diffB = deferred<Response>();
    const patchA = deferred<Response>();
    const patchB = deferred<Response>();

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: [first, second] }));
        }
        if (url.endsWith(`/sessions/${first.session_id}`)) {
          return Promise.resolve(jsonResponse(first));
        }
        if (url.endsWith(`/sessions/${second.session_id}`)) {
          return Promise.resolve(jsonResponse(second));
        }
        if (url.endsWith(`/sessions/${first.session_id}/runs`)) {
          return Promise.resolve(jsonResponse({ session_id: first.session_id, runs: [] }));
        }
        if (url.endsWith(`/sessions/${second.session_id}/runs`)) {
          return Promise.resolve(jsonResponse({ session_id: second.session_id, runs: [] }));
        }
        if (url.includes(`/sessions/${first.session_id}/memory/reviews`)) {
          return Promise.resolve(jsonResponse([]));
        }
        if (url.includes(`/sessions/${second.session_id}/memory/reviews`)) {
          return Promise.resolve(jsonResponse([]));
        }
        if (url.endsWith(`/sessions/${first.session_id}/workspace/diff`)) {
          return diffA.promise;
        }
        if (url.endsWith(`/sessions/${second.session_id}/workspace/diff`)) {
          return diffB.promise;
        }
        if (url.endsWith(`/sessions/${first.session_id}/workspace/patch`)) {
          return patchA.promise;
        }
        if (url.endsWith(`/sessions/${second.session_id}/workspace/patch`)) {
          return patchB.promise;
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /model-diff-a/i }));
    await screen.findByText("idle");
    // Open the diff panel in session A; its fetch stays in flight.
    fireEvent.click(screen.getByRole("button", { name: "Toggle diff panel" }));

    // Switch to session B before A's fetch resolves, then open its diff panel.
    fireEvent.click(screen.getByRole("button", { name: /model-diff-b/i }));
    await screen.findByText("idle");
    fireEvent.click(screen.getByRole("button", { name: "Toggle diff panel" }));

    // B's diff resolves first and applies.
    diffB.resolve(
      jsonResponse({
        session_id: second.session_id,
        files: [{ path: "b-file.ts", status: "modified", additions: 1, deletions: 0 }],
        additions: 1,
        deletions: 0,
      }),
    );
    patchB.resolve(
      jsonResponse({ session_id: second.session_id, format: "unified_diff", patch: "" }),
    );
    expect(await screen.findByText("b-file.ts")).toBeTruthy();

    // A's stale fetch resolves last and must not clobber B's panel.
    diffA.resolve(
      jsonResponse({
        session_id: first.session_id,
        files: [{ path: "a-file.ts", status: "modified", additions: 9, deletions: 9 }],
        additions: 9,
        deletions: 9,
      }),
    );
    patchA.resolve(
      jsonResponse({ session_id: first.session_id, format: "unified_diff", patch: "" }),
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.getByText("b-file.ts")).toBeTruthy();
    expect(screen.queryByText("a-file.ts")).toBeNull();
  });

  it("does not reopen the checkpoints panel when the session changes mid-restore", async () => {
    const first = session("session-restore-0001", "model-restore-a", { checkpoint_count: 1 });
    const second = session("session-restore-0002", "model-restore-b");
    const checkpoint = {
      checkpoint_id: "cp-1",
      tape_id: "tape-1",
      session_id: first.session_id,
      entry_count: 3,
      window_start: 0,
      created_at: "2026-06-12T00:00:00Z",
      label: "before-refactor",
    };
    const reloadSummary = deferred<Response>();
    let firstSummaryCalls = 0;

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: [first, second] }));
        }
        if (url.endsWith(`/sessions/${first.session_id}/checkpoints`)) {
          return Promise.resolve(jsonResponse({ checkpoints: [checkpoint] }));
        }
        if (url.endsWith(`/sessions/${first.session_id}/checkpoints/cp-1/restore`)) {
          return Promise.resolve(jsonResponse({}));
        }
        if (url.endsWith(`/sessions/${first.session_id}`)) {
          firstSummaryCalls += 1;
          // The reload after restore stays in flight until we switch sessions.
          if (firstSummaryCalls > 1) return reloadSummary.promise;
          return Promise.resolve(jsonResponse(first));
        }
        if (url.endsWith(`/sessions/${second.session_id}`)) {
          return Promise.resolve(jsonResponse(second));
        }
        if (url.endsWith(`/sessions/${first.session_id}/runs`)) {
          return Promise.resolve(jsonResponse({ session_id: first.session_id, runs: [] }));
        }
        if (url.endsWith(`/sessions/${second.session_id}/runs`)) {
          return Promise.resolve(jsonResponse({ session_id: second.session_id, runs: [] }));
        }
        if (url.includes(`/sessions/${first.session_id}/memory/reviews`)) {
          return Promise.resolve(jsonResponse([]));
        }
        if (url.includes(`/sessions/${second.session_id}/memory/reviews`)) {
          return Promise.resolve(jsonResponse([]));
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /model-restore-a/i }));
    await screen.findByText("idle");

    // Open the checkpoints panel and restore the checkpoint.
    fireEvent.click(screen.getByRole("button", { name: "Toggle checkpoints panel" }));
    expect(await screen.findByText("before-refactor")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm restore" }));

    // The restore triggers a reload of session A that is still in flight.
    await waitFor(() => expect(firstSummaryCalls).toBe(2));

    // Switch to session B before A's reload resolves.
    fireEvent.click(screen.getByRole("button", { name: /model-restore-b/i }));
    await screen.findByText("idle");

    // A's stale reload resolves now; it must not reopen the checkpoints panel.
    reloadSummary.resolve(jsonResponse(first));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByText(/Checkpoints ·/)).toBeNull();
    expect(
      screen.getByRole("button", { name: "Toggle checkpoints panel" }).getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it("does not reload the new active session when an old checkpoint restore finishes", async () => {
    const first = session("session-restore-0003", "model-restore-c", { checkpoint_count: 1 });
    const second = session("session-restore-0004", "model-restore-d");
    const restoreResponse = deferred<Response>();
    let secondSummaryCalls = 0;

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: [first, second] }));
        }
        if (url.endsWith(`/sessions/${first.session_id}/checkpoints`)) {
          return Promise.resolve(
            jsonResponse({
              checkpoints: [
                {
                  checkpoint_id: "cp-2",
                  tape_id: "tape-2",
                  session_id: first.session_id,
                  entry_count: 4,
                  window_start: 0,
                  created_at: "2026-06-12T00:00:00Z",
                  label: "before-switch",
                },
              ],
            }),
          );
        }
        if (url.endsWith(`/sessions/${first.session_id}/checkpoints/cp-2/restore`)) {
          return restoreResponse.promise;
        }
        if (url.endsWith(`/sessions/${first.session_id}`)) {
          return Promise.resolve(jsonResponse(first));
        }
        if (url.endsWith(`/sessions/${second.session_id}`)) {
          secondSummaryCalls += 1;
          return Promise.resolve(jsonResponse(second));
        }
        if (url.endsWith(`/sessions/${first.session_id}/runs`)) {
          return Promise.resolve(jsonResponse({ session_id: first.session_id, runs: [] }));
        }
        if (url.endsWith(`/sessions/${second.session_id}/runs`)) {
          return Promise.resolve(jsonResponse({ session_id: second.session_id, runs: [] }));
        }
        if (url.includes(`/sessions/${first.session_id}/memory/reviews`)) {
          return Promise.resolve(jsonResponse([]));
        }
        if (url.includes(`/sessions/${second.session_id}/memory/reviews`)) {
          return Promise.resolve(jsonResponse([]));
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /model-restore-c/i }));
    await screen.findByText("idle");
    fireEvent.click(screen.getByRole("button", { name: "Toggle checkpoints panel" }));
    expect(await screen.findByText("before-switch")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm restore" }));

    fireEvent.click(screen.getByRole("button", { name: /model-restore-d/i }));
    await screen.findByText("idle");
    expect(secondSummaryCalls).toBe(1);

    restoreResponse.resolve(jsonResponse({ status: "restored" }));

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Toggle checkpoints panel" }).getAttribute(
          "aria-pressed",
        ),
      ).toBe("false"),
    );
    expect(secondSummaryCalls).toBe(1);
  });

  it("rolls back the optimistic resolve when posting an approval fails", async () => {
    const active = session("session-approval-fail", "model-approval-fail");

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          return jsonResponse({ sessions: [active] });
        }
        if (url.endsWith(`/sessions/${active.session_id}`)) {
          return jsonResponse(active);
        }
        if (url.endsWith(`/sessions/${active.session_id}/runs`)) {
          return jsonResponse({ session_id: active.session_id, runs: [] });
        }
        if (url.includes(`/sessions/${active.session_id}/memory/reviews`)) {
          return jsonResponse([]);
        }
        if (url.includes(`/sessions/${active.session_id}/prompt?event_format=display`)) {
          const payload = {
            source_event_id: "event-approval",
            run_id: "run-approval",
            sequence: 1,
            display_kind: "approval_prompt",
            payload: {
              agent_id: "",
              request_id: "approval-1",
              timeout_seconds: 60,
              tool_call: {
                call_id: "call-1",
                tool_name: "bash",
                arguments: { cmd: "pwd" },
              },
            },
            // Live-streamed prompt: created_at must be "now" so the approval
            // countdown deadline is not already expired when the card mounts.
            created_at: new Date().toISOString(),
          };
          return new Response(
            new ReadableStream({
              start(controller) {
                controller.enqueue(
                  new TextEncoder().encode(
                    `event: approval_prompt\ndata: ${JSON.stringify(payload)}\n\n`,
                  ),
                );
                controller.close();
              },
            }),
            { status: 200, headers: { "Content-Type": "text/event-stream" } },
          );
        }
        if (url.endsWith(`/sessions/${active.session_id}/approve`)) {
          return new Response("boom", { status: 500 });
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /model-approval-fail/i }));
    await screen.findByText("idle");
    fireEvent.change(screen.getByPlaceholderText(/ask the agent/i), {
      target: { value: "run a tool" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Approval Required")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    // The failure is surfaced and the optimistic "approved" state is rolled
    // back so the card becomes actionable again.
    expect(await screen.findByText(/approve failed/)).toBeTruthy();
    expect(screen.queryByText("→ approved")).toBeNull();
    expect(screen.getByRole("button", { name: "Approve" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Deny" })).toBeTruthy();
  });

  it("does not write an old session's approval failure into the new session", async () => {
    const first = session("session-approval-fail-a", "model-approval-a");
    const second = session("session-approval-fail-b", "model-approval-b");
    const approvalResponse = deferred<Response>();

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          return jsonResponse({ sessions: [first, second] });
        }
        if (url.endsWith(`/sessions/${first.session_id}`)) {
          return jsonResponse(first);
        }
        if (url.endsWith(`/sessions/${second.session_id}`)) {
          return jsonResponse(second);
        }
        if (url.endsWith(`/sessions/${first.session_id}/runs`)) {
          return jsonResponse({ session_id: first.session_id, runs: [] });
        }
        if (url.endsWith(`/sessions/${second.session_id}/runs`)) {
          return jsonResponse({ session_id: second.session_id, runs: [] });
        }
        if (url.includes(`/sessions/${first.session_id}/memory/reviews`)) {
          return jsonResponse([]);
        }
        if (url.includes(`/sessions/${second.session_id}/memory/reviews`)) {
          return jsonResponse([]);
        }
        if (url.includes(`/sessions/${first.session_id}/prompt?event_format=display`)) {
          const payload = {
            source_event_id: "event-approval-stale",
            run_id: "run-approval-stale",
            sequence: 1,
            display_kind: "approval_prompt",
            payload: {
              agent_id: "",
              request_id: "approval-stale",
              timeout_seconds: 60,
              tool_call: {
                call_id: "call-stale",
                tool_name: "bash",
                arguments: { cmd: "pwd" },
              },
            },
            created_at: new Date().toISOString(),
          };
          return new Response(
            new ReadableStream({
              start(controller) {
                controller.enqueue(
                  new TextEncoder().encode(
                    `event: approval_prompt\ndata: ${JSON.stringify(payload)}\n\n`,
                  ),
                );
                controller.close();
              },
            }),
            { status: 200, headers: { "Content-Type": "text/event-stream" } },
          );
        }
        if (url.endsWith(`/sessions/${first.session_id}/approve`)) {
          return approvalResponse.promise;
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /model-approval-a/i }));
    await screen.findByText("idle");
    fireEvent.change(screen.getByPlaceholderText(/ask the agent/i), {
      target: { value: "run a tool" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("Approval Required")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    fireEvent.click(screen.getByRole("button", { name: /model-approval-b/i }));
    await screen.findByText("idle");

    approvalResponse.resolve(new Response("boom", { status: 500 }));
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(screen.queryByText(/approve failed/)).toBeNull();
  });
});
