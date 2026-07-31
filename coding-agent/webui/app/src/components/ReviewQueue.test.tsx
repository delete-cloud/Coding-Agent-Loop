// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AgentClient } from "../lib/api";
import ReviewQueue from "./ReviewQueue";
import type { MemoryReviewRecord } from "../lib/types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const record = (overrides: Partial<MemoryReviewRecord> = {}): MemoryReviewRecord => ({
  candidate_id: "cand-1",
  status: "candidate",
  review_reason: null,
  kind: "fact",
  title: "Project uses uv",
  summary: "The project standardizes on uv for dependency management.",
  scope: "project",
  tags: ["python"],
  confidence: 0.92,
  topic_id: null,
  session_id: "s1",
  tape_id: null,
  ...overrides,
});

const jsonResponse = (body: unknown) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });

const client = new AgentClient({ baseUrl: "http://127.0.0.1:18080" });

function stubFetch(handlers: {
  lists: Record<string, MemoryReviewRecord[]>;
  onPost?: (candidateId: string, body: Record<string, unknown>) => void;
}) {
  const listCalls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "POST" && url.includes("/memory/reviews/")) {
        const candidateId = url.split("/memory/reviews/")[1];
        handlers.onPost?.(candidateId, JSON.parse(String(init.body)) as Record<string, unknown>);
        return Promise.resolve(jsonResponse({ candidate_id: candidateId, status: "accepted" }));
      }
      if (url.includes("/memory/reviews")) {
        const status = new URL(url).searchParams.get("status") ?? "";
        listCalls.push(status);
        return Promise.resolve(jsonResponse(handlers.lists[status] ?? []));
      }
      throw new Error(`unexpected fetch ${url}`);
    }),
  );
  return listCalls;
}

describe("ReviewQueue", () => {
  it("defaults to candidates and accepts a candidate, then refreshes the list", async () => {
    const posted: Array<{ candidateId: string; body: Record<string, unknown> }> = [];
    const onChanged = vi.fn();
    const lists: Record<string, MemoryReviewRecord[]> = { candidate: [record()] };
    const listCalls = stubFetch({
      lists,
      onPost: (candidateId, body) => {
        posted.push({ candidateId, body });
        lists.candidate = [];
      },
    });

    render(<ReviewQueue client={client} sessionId="s1" onChanged={onChanged} />);
    fireEvent.click(screen.getByRole("button", { name: /review queue/i }));

    expect(await screen.findByText("Project uses uv")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Candidates" }).getAttribute("aria-selected")).toBe(
      "true",
    );

    fireEvent.click(screen.getByRole("button", { name: "Accept" }));

    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toEqual({
      candidateId: "cand-1",
      body: { status: "accepted", reason: null },
    });
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    // The candidate list was refetched after the transition and is now empty.
    await waitFor(() => expect(listCalls.filter((s) => s === "candidate").length).toBe(2));
    expect(await screen.findByText("No candidates memories")).toBeTruthy();
    expect(screen.queryByText("Project uses uv")).toBeNull();
  });

  it("rejects a candidate with an optional reason via the inline prompt", async () => {
    const posted: Array<{ candidateId: string; body: Record<string, unknown> }> = [];
    const onChanged = vi.fn();
    const lists: Record<string, MemoryReviewRecord[]> = { candidate: [record()] };
    stubFetch({
      lists,
      onPost: (candidateId, body) => {
        posted.push({ candidateId, body });
        lists.candidate = [];
      },
    });

    render(<ReviewQueue client={client} sessionId="s1" onChanged={onChanged} />);
    fireEvent.click(screen.getByRole("button", { name: /review queue/i }));

    expect(await screen.findByText("Project uses uv")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(screen.queryByText("Project uses uv")).toBeTruthy();
    expect(posted).toHaveLength(0);

    fireEvent.change(screen.getByLabelText("reject reason"), {
      target: { value: "outdated advice" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm reject" }));

    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toEqual({
      candidateId: "cand-1",
      body: { status: "rejected", reason: "outdated advice" },
    });
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("No candidates memories")).toBeTruthy();
  });

  it("cancelling the inline reject prompt posts nothing", async () => {
    const onChanged = vi.fn();
    stubFetch({ lists: { candidate: [record()] } });

    render(<ReviewQueue client={client} sessionId="s1" onChanged={onChanged} />);
    fireEvent.click(screen.getByRole("button", { name: /review queue/i }));

    expect(await screen.findByText("Project uses uv")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByLabelText("reject reason")).toBeNull();
    expect(onChanged).not.toHaveBeenCalled();
    expect(screen.getByText("Project uses uv")).toBeTruthy();
  });

  it("falls back to the accepted tab when no candidates exist", async () => {
    const listCalls = stubFetch({
      lists: {
        candidate: [],
        accepted: [record({ candidate_id: "cand-9", status: "accepted", title: "Kept memory" })],
      },
    });

    render(<ReviewQueue client={client} sessionId="s1" onChanged={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: /review queue/i }));

    expect(await screen.findByText("Kept memory")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Accepted" }).getAttribute("aria-selected")).toBe(
      "true",
    );
    expect(listCalls).toEqual(["candidate", "accepted"]);
  });

  it("loads the selected status when switching tabs", async () => {
    const listCalls = stubFetch({
      lists: {
        candidate: [record()],
        rejected: [
          record({
            candidate_id: "cand-7",
            status: "rejected",
            title: "Dropped memory",
            review_reason: "stale",
          }),
        ],
      },
    });

    render(<ReviewQueue client={client} sessionId="s1" onChanged={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: /review queue/i }));

    expect(await screen.findByText("Project uses uv")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Rejected" }));

    expect(await screen.findByText("Dropped memory")).toBeTruthy();
    expect(screen.getByText("reason: stale")).toBeTruthy();
    // Rejected rows carry no accept/reject actions.
    expect(screen.queryByRole("button", { name: "Accept" })).toBeNull();
    expect(listCalls).toEqual(["candidate", "rejected"]);
  });

  it("refreshes the current tab, not the stale one, when the user switches tabs mid-transition", async () => {
    let resolvePost: (() => void) | null = null;
    const lists: Record<string, MemoryReviewRecord[]> = {
      candidate: [record()],
      accepted: [record({ candidate_id: "cand-2", status: "accepted", title: "Kept memory" })],
    };
    const listCalls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (init?.method === "POST" && url.includes("/memory/reviews/")) {
          return new Promise<Response>((resolve) => {
            resolvePost = () =>
              resolve(jsonResponse({ candidate_id: "cand-1", status: "accepted" }));
          });
        }
        if (url.includes("/memory/reviews")) {
          const status = new URL(url).searchParams.get("status") ?? "";
          listCalls.push(status);
          return Promise.resolve(jsonResponse(lists[status] ?? []));
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    render(<ReviewQueue client={client} sessionId="s1" onChanged={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: /review queue/i }));
    expect(await screen.findByText("Project uses uv")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    // Switch tabs while the transition POST is still in flight.
    fireEvent.click(screen.getByRole("tab", { name: "Accepted" }));
    expect(await screen.findByText("Kept memory")).toBeTruthy();

    expect(resolvePost).not.toBeNull();
    await act(async () => {
      resolvePost!();
    });

    // The post-transition reload targeted the accepted tab, and the stale
    // candidate list never overwrote the accepted records.
    await waitFor(() => expect(listCalls[listCalls.length - 1]).toBe("accepted"));
    expect(screen.getByText("Kept memory")).toBeTruthy();
    expect(screen.queryByText("Project uses uv")).toBeNull();
  });
});
