// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

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
        if (url.includes(`/sessions/${first.session_id}/resume?event_format=display`)) {
          activePromptSignal = init?.signal ?? undefined;
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
    fireEvent.click(screen.getByRole("button", { name: /model-b/i }));

    expect(activePromptSignal?.aborted).toBe(true);
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
});
