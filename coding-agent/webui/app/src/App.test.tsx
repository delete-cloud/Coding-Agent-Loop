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
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<App />);

    const baseUrl = screen.getByTitle("Server base URL") as HTMLInputElement;
    await waitFor(() => expect(sessionsUrl).toBe(`${window.location.origin}/sessions`));
    expect(baseUrl.value).toBe(window.location.origin);
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
            created_at: "2026-06-12T00:00:00Z",
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
    vi.stubGlobal("confirm", vi.fn(() => true));

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: [doomed] }));
        }
        if (url.endsWith(`/sessions/${doomed.session_id}`) && init?.method === "DELETE") {
          deletePath = url;
          deleteMethod = init.method;
          return Promise.resolve(
            jsonResponse({ status: "closed", session_id: doomed.session_id }),
          );
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
});
