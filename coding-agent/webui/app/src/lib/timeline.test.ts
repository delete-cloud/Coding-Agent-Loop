import { describe, expect, it } from "vitest";
import type { DisplayStreamEvent } from "./types";
import {
  applyEvent,
  replayEvents,
  toolArgSummary,
  toolDuration,
  type TimelineItem,
} from "./timeline";

const withoutIds = (items: TimelineItem[]) =>
  items.map(({ id: _id, ...item }) => item);

const displayEvent = (
  event: DisplayStreamEvent["event"],
  payload: Record<string, unknown>,
  sequence: number,
  createdAt = "2026-06-12T00:00:00Z",
): DisplayStreamEvent => ({
  event,
  data: {
    source_event_id: `event-${sequence}`,
    run_id: "run-1",
    sequence,
    display_kind: event,
    payload,
    created_at: createdAt,
  },
} as DisplayStreamEvent);

describe("replayEvents", () => {
  it("uses the same reducer path as streamed events", () => {
    const events: DisplayStreamEvent[] = [
      displayEvent("assistant_text_delta", { agent_id: "", content: "hello " }, 1),
      displayEvent("assistant_text_delta", { agent_id: "", content: "world" }, 2),
      displayEvent("tool_call", { agent_id: "", call_id: "call-1", tool_name: "bash", arguments: { cmd: "pwd" } }, 3),
      displayEvent("tool_result", { agent_id: "", call_id: "call-1", tool_name: "bash", display_result: "/repo", is_error: false }, 4, "2026-06-12T00:00:02.500Z"),
      displayEvent("final_result", { agent_id: "", turn_id: "turn-1", completion_status: "completed" }, 5),
    ];

    const seed: TimelineItem[] = [{ id: "user-1", kind: "user", text: "hi" }];
    const streamed = events.reduce(applyEvent, seed);

    expect(withoutIds(replayEvents(seed, events))).toEqual(withoutIds(streamed));

    const toolItem = replayEvents(seed, events).find((it) => it.kind === "tool");
    expect(toolItem).toMatchObject({
      kind: "tool",
      callId: "call-1",
      toolName: "bash",
      startedAt: "2026-06-12T00:00:00Z",
      finishedAt: "2026-06-12T00:00:02.500Z",
    });
  });

  it("marks approval prompts resolved when replaying approval results", () => {
    const events: DisplayStreamEvent[] = [
      displayEvent(
        "approval_prompt",
        {
          agent_id: "",
          request_id: "approval-1",
          tool_call: { call_id: "call-1", tool_name: "bash", arguments: { cmd: "pwd" } },
          timeout_seconds: 30,
        },
        1,
      ),
      displayEvent("approval_result", { agent_id: "", request_id: "approval-1", approved: true }, 2),
    ];

    expect(withoutIds(replayEvents([], events))).toEqual([
      {
        kind: "approval",
        agentId: "",
        requestId: "approval-1",
        toolName: "bash",
        args: { cmd: "pwd" },
        timeoutSeconds: 30,
        promptedAt: "2026-06-12T00:00:00Z",
        resolved: "approved",
      },
    ]);
  });

  it("leaves approval prompts unresolved when replaying malformed approval results", () => {
    const events: DisplayStreamEvent[] = [
      displayEvent(
        "approval_prompt",
        {
          agent_id: "",
          request_id: "approval-1",
          tool_call: { call_id: "call-1", tool_name: "bash", arguments: { cmd: "pwd" } },
        },
        1,
      ),
      displayEvent("approval_result", { agent_id: "", request_id: "approval-1" }, 2),
    ];

    expect(withoutIds(replayEvents([], events))).toEqual([
      {
        kind: "approval",
        agentId: "",
        requestId: "approval-1",
        toolName: "bash",
        args: { cmd: "pwd" },
        promptedAt: "2026-06-12T00:00:00Z",
      },
    ]);
  });
});


describe("toolArgSummary", () => {
  it("prefers path-like and command-like arguments", () => {
    expect(toolArgSummary("read_file", { path: "src/a.ts", limit: 10 })).toBe("src/a.ts");
    expect(toolArgSummary("bash", { cmd: "npm test" })).toBe("npm test");
  });

  it("falls back to the first scalar argument and collapses whitespace", () => {
    expect(toolArgSummary("grep", { pattern: "foo  bar\nbaz" })).toBe("foo bar baz");
    expect(toolArgSummary("custom", { limit: 42 })).toBe("42");
  });

  it("truncates long values to one line", () => {
    const summary = toolArgSummary("bash", { cmd: "x".repeat(200) });
    expect(summary.length).toBe(80);
    expect(summary.endsWith("…")).toBe(true);
  });

  it("returns empty string when no scalar argument exists", () => {
    expect(toolArgSummary("noop", { nested: { a: 1 } })).toBe("");
  });
});

describe("toolDuration", () => {
  it("derives a duration from call/result timestamps", () => {
    expect(
      toolDuration({
        startedAt: "2026-06-12T00:00:00Z",
        finishedAt: "2026-06-12T00:00:02.500Z",
      }),
    ).toBe("2.5s");
  });

  it("returns null when timestamps are missing", () => {
    expect(toolDuration({ startedAt: "2026-06-12T00:00:00Z" })).toBeNull();
    expect(toolDuration({})).toBeNull();
  });
});
