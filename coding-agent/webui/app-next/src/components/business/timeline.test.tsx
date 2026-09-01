import { fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it } from "vitest";

import zhMessages from "../../../messages/zh.json";
import { Timeline, TrajectoryLedger } from "@/components/business/timeline";
import type { TimelineMessage } from "@/hooks/use-connected-chat";

const AT = "2026-08-24T14:02:00Z";

function msg(
  partial: Partial<TimelineMessage> & Pick<TimelineMessage, "id" | "kind" | "body">,
): TimelineMessage {
  return { createdAt: AT, ...partial };
}

function renderTimeline(props: Partial<Parameters<typeof Timeline>[0]> = {}) {
  return render(
    <NextIntlClientProvider locale="zh" messages={zhMessages}>
      <Timeline {...props} />
    </NextIntlClientProvider>,
  );
}

function sections(container: HTMLElement) {
  return container.querySelectorAll(".timeline > section");
}

function roleOf(section: Element) {
  return section.querySelector(".role")?.textContent;
}

describe("Timeline dynamic messages", () => {
  it("renders user, assistant, thinking, and progress messages with role labels and stable ids", () => {
    const messages: TimelineMessage[] = [
      msg({ id: "e1", kind: "user", body: "split the shell" }),
      msg({ id: "e2", kind: "assistant", body: "landed per spec" }),
      msg({ id: "e3", kind: "thinking", body: "weighing hairlines" }),
      msg({ id: "e4", kind: "progress", body: "compiling", progress: { current: 3, total: 10 } }),
    ];
    const { container } = renderTimeline({ messages });

    const rendered = sections(container);
    expect(rendered).toHaveLength(4);
    // Stable logical identity is the primary source_event_id.
    expect([...rendered].map((s) => s.getAttribute("data-message-id"))).toEqual([
      "e1",
      "e2",
      "e3",
      "e4",
    ]);
    expect(roleOf(rendered[0])).toBe(zhMessages.timeline.userRole);
    expect(roleOf(rendered[1])).toBe(zhMessages.timeline.assistantRole);
    expect(roleOf(rendered[2])).toBe(zhMessages.timeline.thinkingRole);
    expect(roleOf(rendered[3])).toBe(zhMessages.timeline.progressRole);

    expect(screen.getByText("split the shell")).toBeDefined();
    expect(screen.getByText("landed per spec")).toBeDefined();
    expect(screen.getByText("weighing hairlines")).toBeDefined();
    // Progress renders its label and the current/total counter.
    expect(screen.getByText("compiling")).toBeDefined();
    expect(screen.getByText("3 / 10")).toBeDefined();
  });

  it("renders a pending tool call with its arguments and a pending note", () => {
    const { container } = renderTimeline({
      messages: [
        msg({
          id: "t1",
          kind: "tool",
          body: "run_tests",
          toolName: "run_tests",
          toolArguments: '{"path":"."}',
        }),
      ],
    });

    const rendered = sections(container);
    expect(rendered).toHaveLength(1);
    expect(roleOf(rendered[0])).toBe(zhMessages.timeline.toolRole);
    expect(screen.getByText("run_tests")).toBeDefined();
    expect(screen.getByText('{"path":"."}')).toBeDefined();
    expect(screen.getByText(zhMessages.timeline.toolPending)).toBeDefined();
  });
  it("renders child approval as a visible noninteractive timeline item", () => {
    const { container } = renderTimeline({
      messages: [
        msg({
          id: "approval-1",
          kind: "approval",
          body: "write_file",
          toolName: "write_file",
          toolArguments: '{"path":"src/example.py"}',
          approvalRequestId: "approval-01",
          effectId: "effect-child-01",
          attemptId: "attempt-child-01",
          approvalTargetRunId: "child-run-1",
          approvalTargetParentEffectId: "effect-child-01",
        }),
      ],
    });

    const rendered = sections(container);
    expect(rendered).toHaveLength(1);
    expect(roleOf(rendered[0])).toBe(zhMessages.timeline.approvalRole);
    expect(screen.getByText(zhMessages.timeline.approvalRequired)).toBeDefined();
    expect(screen.getByText("write_file")).toBeDefined();
    expect(screen.getByText('{"path":"src/example.py"}')).toBeDefined();
    expect(screen.getByText("approval-01")).toBeDefined();
    expect(screen.getAllByText("effect-child-01")).toHaveLength(2);
    expect(screen.getByText("attempt-child-01")).toBeDefined();
    expect(screen.getByText("child-run-1")).toBeDefined();
    expect(screen.getByText(zhMessages.timeline.approvalChildTarget)).toBeDefined();
    expect(rendered[0].querySelector("button")).toBeNull();
  });


  it("renders a tool result in place of the pending note", () => {
    renderTimeline({
      messages: [
        msg({
          id: "t2",
          kind: "tool",
          body: "run_tests",
          toolName: "run_tests",
          toolOutput: "12 passed",
          toolError: false,
        }),
      ],
    });

    expect(screen.getByText("12 passed")).toBeDefined();
    expect(screen.queryByText(zhMessages.timeline.toolPending)).toBeNull();
  });

  it("renders an errored tool result with the error role label", () => {
    const { container } = renderTimeline({
      messages: [
        msg({
          id: "t3",
          kind: "tool",
          body: "deploy",
          toolName: "deploy",
          toolOutput: "permission denied",
          toolError: true,
        }),
      ],
    });

    expect(roleOf(sections(container)[0])).toBe(zhMessages.timeline.toolErrorRole);
    expect(screen.getByText("permission denied")).toBeDefined();
  });

  it("renders every terminal outcome with its own role label", () => {
    const { container } = renderTimeline({
      messages: [
        msg({ id: "x1", kind: "terminal", body: "done", terminalOutcome: "completed" }),
        msg({ id: "x2", kind: "terminal", body: "Adapter failed", terminalOutcome: "failed" }),
        msg({ id: "x3", kind: "terminal", body: "", terminalOutcome: "cancelled" }),
        msg({ id: "x4", kind: "terminal", body: "", terminalOutcome: "interrupted" }),
      ],
    });

    const rendered = sections(container);
    expect(rendered).toHaveLength(4);
    expect(roleOf(rendered[0])).toBe(zhMessages.timeline.terminalCompletedRole);
    expect(roleOf(rendered[1])).toBe(zhMessages.timeline.terminalFailedRole);
    expect(roleOf(rendered[2])).toBe(zhMessages.timeline.terminalCancelledRole);
    expect(roleOf(rendered[3])).toBe(zhMessages.timeline.terminalInterruptedRole);
    expect(screen.getByText("done")).toBeDefined();
    expect(screen.getByText("Adapter failed")).toBeDefined();
  });

  it("keeps message identity stable when new messages arrive", () => {
    const first: TimelineMessage[] = [msg({ id: "e1", kind: "user", body: "one" })];
    const { container, rerender } = render(
      <NextIntlClientProvider locale="zh" messages={zhMessages}>
        <Timeline messages={first} />
      </NextIntlClientProvider>,
    );
    expect([...sections(container)].map((s) => s.getAttribute("data-message-id"))).toEqual(["e1"]);

    rerender(
      <NextIntlClientProvider locale="zh" messages={zhMessages}>
        <Timeline
          messages={[...first, msg({ id: "e2", kind: "assistant", body: "two" })]}
        />
      </NextIntlClientProvider>,
    );
    expect([...sections(container)].map((s) => s.getAttribute("data-message-id"))).toEqual([
      "e1",
      "e2",
    ]);
  });
});

describe("Timeline states", () => {
  it("shows the loading note instead of messages while loading", () => {
    const { container } = renderTimeline({ messages: [], status: "loading" });

    expect(screen.getByText(zhMessages.timeline.loading)).toBeDefined();
    expect(container.querySelectorAll("[data-message-id]")).toHaveLength(0);
  });

  it("shows the empty note when ready with zero messages", () => {
    renderTimeline({ messages: [], status: "ready" });

    expect(screen.getByText(zhMessages.timeline.empty)).toBeDefined();
  });

  it("shows an alert error note and keeps stale messages visible", () => {
    const { container } = renderTimeline({
      messages: [msg({ id: "e1", kind: "user", body: "still here" })],
      status: "error",
      error: new Error("snapshot 500"),
    });

    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain(zhMessages.timeline.error);
    expect(alert.textContent).toContain("snapshot 500");
    // Stale-but-valid messages stay visible under the error note.
    expect(screen.getByText("still here")).toBeDefined();
    expect(container.querySelectorAll("[data-message-id]")).toHaveLength(1);
  });

  it("shows the reconnecting note while keeping messages visible", () => {
    renderTimeline({
      messages: [msg({ id: "e1", kind: "assistant", body: "partial answer" })],
      status: "reconnecting",
    });

    expect(screen.getByText(zhMessages.timeline.reconnecting)).toBeDefined();
    expect(screen.getByText("partial answer")).toBeDefined();
  });

  it("shows the replay-required note with its reason", () => {
    renderTimeline({
      messages: [msg({ id: "e1", kind: "assistant", body: "before the gap" })],
      status: "replay_required",
      replayReason: "sequence_loss",
    });

    expect(screen.getByText(zhMessages.timeline.replayRequired)).toBeDefined();
    expect(screen.getByText("sequence_loss")).toBeDefined();
    expect(screen.getByText("before the gap")).toBeDefined();
  });

  it("renders state affordances inside the single .timeline container (no new scroll surface)", () => {
    const { container } = renderTimeline({ messages: [], status: "loading" });

    expect(container.querySelectorAll(".timeline")).toHaveLength(1);
    expect(container.querySelector(".timeline-scroll")).toBeNull();
    expect(container.querySelector(".timeline")?.textContent).toContain(
      zhMessages.timeline.loading,
    );
  });
});

describe("Timeline static shell default", () => {
  it("renders placeholder messages without invented telemetry when no props are given", () => {
    const { container } = renderTimeline();

    expect(screen.getByText(zhMessages.timeline.m1.body)).toBeDefined();
    expect(screen.getByText(zhMessages.timeline.m2.body)).toBeDefined();
    expect(screen.getByText(zhMessages.timeline.m3.body)).toBeDefined();
    expect(screen.getByText(zhMessages.timeline.m4.body)).toBeDefined();
    expect(container.querySelector(".msg-meta")).toBeNull();

    const rendered = sections(container);
    expect(rendered).toHaveLength(4);
    expect(roleOf(rendered[0])).toBe(zhMessages.timeline.userRole);
    expect(roleOf(rendered[1])).toBe(zhMessages.timeline.assistantRole);
  });
});

describe("Trajectory ledger", () => {
  it("renders kind, seq, summary, and expands payload on click", () => {
    const { container } = render(
      <NextIntlClientProvider locale="zh" messages={zhMessages}>
        <TrajectoryLedger
          messages={[
            msg({ id: "e1", kind: "user", body: "hello" }),
            msg({
              id: "e2",
              kind: "terminal",
              body: "Adapter failed",
              terminalOutcome: "failed",
            }),
          ]}
          status="ready"
        />
      </NextIntlClientProvider>,
    );

    const rows = container.querySelectorAll(".trajectory-row");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("user");
    expect(rows[0].textContent).toContain("hello");
    expect(rows[1].textContent).toContain("terminal");
    expect(rows[1].textContent).toContain("Adapter failed");

    fireEvent.click(rows[1]);
    expect(container.querySelector(".trajectory-payload")?.textContent).toContain("Adapter failed");
  });
});
