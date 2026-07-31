// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Timeline from "./Timeline";
import type { TimelineItem } from "../lib/timeline";

type ToolItem = Extract<TimelineItem, { kind: "tool" }>;

const tool = (
  id: string,
  toolName: string,
  args: Record<string, unknown>,
  extra: Partial<ToolItem> = {},
): ToolItem => ({
  id,
  kind: "tool",
  agentId: "",
  callId: id,
  toolName,
  args,
  ...extra,
});

const noop = vi.fn();

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("tool-call cards", () => {
  it("groups consecutive tool calls under a collapsible count header", () => {
    const items: TimelineItem[] = [
      { id: "u1", kind: "user", text: "go" },
      tool("t1", "read_file", { path: "src/a.ts" }),
      tool("t2", "write_file", { path: "src/b.ts" }),
      tool("t3", "bash", { cmd: "npm test" }),
      { id: "a1", kind: "assistant", agentId: "", text: "done" },
    ];
    render(<Timeline items={items} onApprove={noop} showThinking />);

    const header = screen.getByRole("button", { name: /3 tool calls/ });
    expect(header.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("read_file")).toBeTruthy();
    expect(screen.getByText("bash")).toBeTruthy();

    fireEvent.click(header);
    expect(header.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("read_file")).toBeNull();
    expect(screen.queryByText("bash")).toBeNull();

    fireEvent.click(header);
    expect(screen.getByText("read_file")).toBeTruthy();
  });

  it("does not group tool calls separated by assistant text", () => {
    const items: TimelineItem[] = [
      tool("t1", "read_file", { path: "src/a.ts" }),
      { id: "a1", kind: "assistant", agentId: "", text: "note" },
      tool("t2", "bash", { cmd: "pwd" }),
    ];
    render(<Timeline items={items} onApprove={noop} showThinking />);

    expect(screen.queryByText(/tool calls/)).toBeNull();
    expect(screen.getByText("read_file")).toBeTruthy();
    expect(screen.getByText("bash")).toBeTruthy();
  });

  it("keeps card output collapsed until expanded, with summary and duration", () => {
    const items: TimelineItem[] = [
      tool("t1", "bash", { cmd: "npm test" }, {
        result: "ok",
        startedAt: "2026-06-12T00:00:00Z",
        finishedAt: "2026-06-12T00:00:02.500Z",
      }),
    ];
    render(<Timeline items={items} onApprove={noop} showThinking />);

    // Summary and duration on the collapsed header; output hidden.
    expect(screen.getByText("npm test")).toBeTruthy();
    expect(screen.getByText("2.5s")).toBeTruthy();
    expect(screen.queryByText("ok")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /bash/ }));
    expect(screen.getByText("ok")).toBeTruthy();
  });

  it("shows a running indicator while the result is pending", () => {
    const items: TimelineItem[] = [tool("t1", "bash", { cmd: "sleep 5" })];
    render(<Timeline items={items} onApprove={noop} showThinking />);

    expect(screen.getByTitle("running")).toBeTruthy();
  });

  it("tags cards from non-root agents as subagent", () => {
    const items: TimelineItem[] = [
      tool("t1", "bash", { cmd: "pwd" }, { agentId: "worker-1" }),
    ];
    render(<Timeline items={items} onApprove={noop} showThinking />);

    expect(screen.getByText("subagent")).toBeTruthy();
    expect(screen.getByText("[worker-1]")).toBeTruthy();
  });
});

describe("thinking visibility", () => {
  const items: TimelineItem[] = [
    { id: "th1", kind: "thinking", agentId: "", text: "reasoning…" },
    { id: "a1", kind: "assistant", agentId: "", text: "answer" },
  ];

  it("shows thinking blocks by default", () => {
    render(<Timeline items={items} onApprove={noop} showThinking />);
    expect(screen.getByText("reasoning…")).toBeTruthy();
  });

  it("hides thinking blocks when showThinking is false", () => {
    render(<Timeline items={items} onApprove={noop} showThinking={false} />);
    expect(screen.queryByText("reasoning…")).toBeNull();
    expect(screen.getByText("answer")).toBeTruthy();
  });
});

describe("approval card", () => {
  const approval = (extra: Partial<Extract<TimelineItem, { kind: "approval" }>> = {}) =>
    ({
      id: "ap1",
      kind: "approval",
      agentId: "",
      requestId: "req-1",
      toolName: "bash",
      args: { cmd: "rm -rf build" },
      ...extra,
    }) as TimelineItem;

  it("sends scope=session for always allow", () => {
    const onApprove = vi.fn();
    render(
      <Timeline
        items={[approval({ timeoutSeconds: 120 })]}
        onApprove={onApprove}
        showThinking
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Always allow (this session)" }));
    expect(onApprove).toHaveBeenCalledWith("req-1", true, "", "session");
  });

  it("sends scope=once for approve and deny", () => {
    const onApprove = vi.fn();
    render(
      <Timeline
        items={[approval({ timeoutSeconds: 120 })]}
        onApprove={onApprove}
        showThinking
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(onApprove).toHaveBeenCalledWith("req-1", true, "", "once");
    fireEvent.click(screen.getByRole("button", { name: "Deny" }));
    expect(onApprove).toHaveBeenCalledWith("req-1", false, "", "once");
  });

  it("counts down and shows a timed-out state on expiry", () => {
    vi.useFakeTimers();
    render(
      <Timeline
        items={[approval({ timeoutSeconds: 2 })]}
        onApprove={noop}
        showThinking
      />,
    );

    expect(screen.getByText("0:02")).toBeTruthy();
    act(() => vi.advanceTimersByTime(1000));
    expect(screen.getByText("0:01")).toBeTruthy();
    act(() => vi.advanceTimersByTime(1000));

    expect(screen.getByText(/timed out/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });

  it("shows the timed-out state immediately for a replayed prompt past its deadline", () => {
    // Prompt created long ago: the server already auto-denied on timeout, so
    // the countdown must not restart from the full value on replay.
    render(
      <Timeline
        items={[
          approval({
            timeoutSeconds: 30,
            promptedAt: "2020-01-01T00:00:00Z",
          }),
        ]}
        onApprove={noop}
        showThinking
      />,
    );

    expect(screen.getByText(/timed out/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });

  it("counts down from promptedAt, not from mount", () => {
    // Prompt created 10s ago with a 30s timeout: reopening the session shows
    // ~20s remaining, not the full 30s.
    render(
      <Timeline
        items={[
          approval({
            timeoutSeconds: 30,
            promptedAt: new Date(Date.now() - 10_000).toISOString(),
          }),
        ]}
        onApprove={noop}
        showThinking
      />,
    );

    expect(screen.getByText("0:20")).toBeTruthy();
  });

  it("reflects wall-clock jumps, not just timer ticks", () => {
    // Background-tab throttling: the interval may lag, but the remaining time
    // is recomputed from Date.now() against the stored deadline.
    vi.useFakeTimers();
    render(
      <Timeline
        items={[
          approval({
            timeoutSeconds: 90,
            promptedAt: new Date().toISOString(),
          }),
        ]}
        onApprove={noop}
        showThinking
      />,
    );

    expect(screen.getByText("1:30")).toBeTruthy();
    // Jump the wall clock 60s ahead, then let one (late) tick fire.
    act(() => {
      vi.setSystemTime(Date.now() + 60_000);
      vi.advanceTimersByTime(1000);
    });
    expect(screen.getByText("0:29")).toBeTruthy();
  });
});
