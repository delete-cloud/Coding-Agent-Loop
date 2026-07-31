// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import SessionList from "./SessionList";
import type { SessionSummary } from "../lib/types";

afterEach(cleanup);

const session = (
  id: string,
  overrides: Partial<SessionSummary> = {},
): SessionSummary => ({
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
  model_name: "model-x",
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

function renderList(sessions: SessionSummary[]) {
  return render(
    <SessionList
      sessions={sessions}
      activeSessionId={null}
      loading={false}
      error={null}
      open
      onRefresh={() => undefined}
      onSelect={() => undefined}
      onDelete={() => undefined}
      onNewSession={() => undefined}
    />,
  );
}

describe("SessionList status dots", () => {
  it("maps session states to dot color semantics", () => {
    renderList([
      session("session-running-0001", { status: "running" }),
      session("session-pending-0001", { status: "waiting_approval", pending_approval: true }),
      session("session-failed-0001", { status: "failed" }),
      session("session-done-0001", { status: "completed" }),
    ]);

    const running = screen.getByTitle("status: running");
    expect(running.className).toContain("bg-accent");
    expect(running.className).toContain("animate-pulse");

    expect(screen.getByTitle("status: pending").className).toContain("bg-warn");
    expect(screen.getByTitle("status: failed").className).toContain("bg-err");
    expect(screen.getByTitle("status: idle").className).toContain("bg-muted");
  });
});

describe("SessionList checkpoint badge", () => {
  it("shows the badge only when checkpoint_count is positive", () => {
    renderList([
      session("session-with-ckpt-01", { checkpoint_count: 3 }),
      session("session-no-ckpt-0001"),
    ]);

    expect(screen.getByText("⎘ 3")).toBeTruthy();
    expect(screen.queryByText("⎘ 0")).toBeNull();
  });
});
