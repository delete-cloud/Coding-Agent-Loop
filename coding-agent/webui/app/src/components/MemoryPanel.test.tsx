// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import MemoryPanel, { extractRecallHits } from "./MemoryPanel";
import type { ContextPackItem, MemoryReviewRecord, RuntimeRun } from "../lib/types";

afterEach(cleanup);

const hit = (overrides: Partial<ContextPackItem> = {}): ContextPackItem => ({
  source_kind: "memory",
  source_id: "mem-1",
  label: "User prefers uv for Python deps",
  score: 0.8734,
  score_scale: "similarity",
  ...overrides,
});

const memory = (overrides: Partial<MemoryReviewRecord> = {}): MemoryReviewRecord => ({
  candidate_id: "cand-1",
  status: "accepted",
  review_reason: null,
  kind: "fact",
  title: "Project uses uv",
  summary: "The project standardizes on uv for dependency management.",
  scope: "project",
  tags: ["python", "uv"],
  confidence: 0.92,
  topic_id: null,
  session_id: "session-1",
  tape_id: null,
  ...overrides,
});

const run = (metadata: Record<string, unknown>): RuntimeRun => ({
  run_id: "run-1",
  session_id: "session-1",
  tape_id: null,
  parent_run_id: null,
  agent_id: null,
  status: "completed",
  started_at: "2026-06-12T00:00:00Z",
  ended_at: null,
  metadata,
  result: {},
  error: null,
});

describe("MemoryPanel", () => {
  it("renders recall hits with score and score-scale badge", () => {
    render(
      <MemoryPanel
        hits={[
          hit(),
          hit({
            source_id: "mem-2",
            label: "Repo layout note",
            score: null,
            score_scale: "overlap",
          }),
        ]}
        memories={[]}
        loading={false}
        error={null}
      />,
    );

    expect(screen.getByText("User prefers uv for Python deps")).toBeTruthy();
    expect(screen.getByText("0.87")).toBeTruthy();
    expect(screen.getByText("similarity")).toBeTruthy();
    expect(screen.getByText("overlap")).toBeTruthy();
    expect(screen.getByText("—")).toBeTruthy();
    expect(screen.getAllByText("memory").length).toBeGreaterThan(0);
  });

  it("renders accepted memories with kind, confidence, and tags", () => {
    render(<MemoryPanel hits={[]} memories={[memory()]} loading={false} error={null} />);

    expect(screen.getByText("Project uses uv")).toBeTruthy();
    expect(
      screen.getByText("The project standardizes on uv for dependency management."),
    ).toBeTruthy();
    expect(screen.getByText("fact")).toBeTruthy();
    expect(screen.getByText("conf 0.92")).toBeTruthy();
    expect(screen.getByText("python")).toBeTruthy();
    expect(screen.getByText("uv")).toBeTruthy();
  });

  it("shows empty states when there are no hits or memories", () => {
    render(<MemoryPanel hits={[]} memories={[]} loading={false} error={null} />);

    expect(screen.getByText("No recall hits")).toBeTruthy();
    expect(screen.getByText("No accepted memories")).toBeTruthy();
  });

  it("surfaces load errors without clearing the panel", () => {
    render(
      <MemoryPanel hits={[]} memories={[]} loading={false} error="memory load failed: 500 boom" />,
    );

    expect(screen.getByText("memory load failed: 500 boom")).toBeTruthy();
  });
});

describe("extractRecallHits", () => {
  it("flattens context_pack sections across runs", () => {
    const runs = [
      run({
        context_pack: {
          title: "Context Pack",
          sections: [
            { title: "Memories", items: [hit()] },
            { title: "Tape", items: [hit({ source_id: "mem-2", source_kind: "tape" })] },
          ],
        },
      }),
      run({
        context_pack: {
          title: "Context Pack",
          sections: [{ title: "Memories", items: [hit({ source_id: "mem-3" })] }],
        },
      }),
    ];

    const hits = extractRecallHits(runs);
    expect(hits).toHaveLength(3);
    expect(hits.map((h) => h.source_id)).toEqual(["mem-1", "mem-2", "mem-3"]);
  });

  it("returns an empty list when runs have no context_pack", () => {
    expect(extractRecallHits([run({}), run({ context_pack: null })])).toEqual([]);
    expect(extractRecallHits([])).toEqual([]);
  });
});
