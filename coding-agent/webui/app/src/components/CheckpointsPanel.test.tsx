// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AgentClient } from "../lib/api";
import CheckpointsPanel from "./CheckpointsPanel";
import type { CheckpointMetadata } from "../lib/types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const checkpoint = (overrides: Partial<CheckpointMetadata> = {}): CheckpointMetadata => ({
  checkpoint_id: "ckpt-0001",
  tape_id: "tape-1",
  session_id: "s1",
  entry_count: 12,
  window_start: 0,
  created_at: "2026-06-12T10:00:00Z",
  label: "before refactor",
  ...overrides,
});

const jsonResponse = (body: unknown) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });

const client = new AgentClient({ baseUrl: "http://127.0.0.1:18080" });

function stubFetch(handlers: {
  list: () => CheckpointMetadata[];
  onCapture?: (body: Record<string, unknown>) => void;
  onRestore?: (checkpointId: string) => void;
}) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/checkpoints/") && url.endsWith("/restore") && init?.method === "POST") {
        const checkpointId = url.split("/checkpoints/")[1].replace(/\/restore$/, "");
        calls.push(`restore:${checkpointId}`);
        handlers.onRestore?.(checkpointId);
        return Promise.resolve(
          jsonResponse({ status: "restored", session_id: "s1", checkpoint_id: checkpointId }),
        );
      }
      if (url.endsWith("/sessions/s1/checkpoints") && init?.method === "POST") {
        calls.push("capture");
        handlers.onCapture?.(JSON.parse(String(init.body)) as Record<string, unknown>);
        return Promise.resolve(jsonResponse(checkpoint({ checkpoint_id: "ckpt-0002" })));
      }
      if (url.endsWith("/sessions/s1/checkpoints")) {
        calls.push("list");
        return Promise.resolve(jsonResponse({ checkpoints: handlers.list() }));
      }
      throw new Error(`unexpected fetch ${url}`);
    }),
  );
  return calls;
}

describe("CheckpointsPanel", () => {
  it("lists checkpoints with label, id, and entry count", async () => {
    stubFetch({ list: () => [checkpoint()] });

    render(<CheckpointsPanel client={client} sessionId="s1" onRestored={() => undefined} />);

    expect(await screen.findByText("before refactor")).toBeTruthy();
    expect(screen.getByText(/ckpt-0001 · 12 entries/)).toBeTruthy();
    expect(screen.getByText("Checkpoints · 1")).toBeTruthy();
  });

  it("creates a checkpoint with an optional label and refreshes the list", async () => {
    const captured: Array<Record<string, unknown>> = [];
    const items: CheckpointMetadata[] = [];
    const onCaptured = vi.fn();
    const calls = stubFetch({
      list: () => items,
      onCapture: (body) => {
        captured.push(body);
        items.push(checkpoint({ checkpoint_id: "ckpt-0002", label: body.label as string }));
      },
    });

    render(
      <CheckpointsPanel
        client={client}
        sessionId="s1"
        onRestored={() => undefined}
        onCaptured={onCaptured}
      />,
    );

    expect(await screen.findByText("No checkpoints")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("checkpoint label"), {
      target: { value: "pre-migration" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create checkpoint" }));

    await waitFor(() => expect(captured).toEqual([{ label: "pre-migration" }]));
    expect(await screen.findByText("pre-migration")).toBeTruthy();
    expect(onCaptured).toHaveBeenCalledTimes(1);
    expect(calls.filter((c) => c === "list")).toHaveLength(2);
  });

  it("requires confirmation before restoring and reloads via onRestored", async () => {
    const restored: string[] = [];
    const onRestored = vi.fn();
    stubFetch({ list: () => [checkpoint()], onRestore: (id) => restored.push(id) });

    render(<CheckpointsPanel client={client} sessionId="s1" onRestored={onRestored} />);

    expect(await screen.findByText("before refactor")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));

    // Confirm step states the exact restore boundary before anything is posted.
    expect(screen.getByText(/rewinds session history and runtime settings/i)).toBeTruthy();
    expect(screen.getByText(/workspace files are not restored/i)).toBeTruthy();
    expect(restored).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Confirm restore" }));

    await waitFor(() => expect(restored).toEqual(["ckpt-0001"]));
    await waitFor(() => expect(onRestored).toHaveBeenCalledTimes(1));
  });

  it("cancelling the confirm step posts nothing", async () => {
    const restored: string[] = [];
    stubFetch({ list: () => [checkpoint()], onRestore: (id) => restored.push(id) });

    render(<CheckpointsPanel client={client} sessionId="s1" onRestored={() => undefined} />);

    expect(await screen.findByText("before refactor")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByText(/workspace files are not restored/i)).toBeNull();
    expect(restored).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Restore" })).toBeTruthy();
  });
});
