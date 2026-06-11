import { describe, expect, it } from "vitest";
import type { DisplayStreamEvent } from "./types";
import { applyEvent, replayEvents, type TimelineItem } from "./timeline";

const withoutIds = (items: TimelineItem[]) =>
  items.map(({ id: _id, ...item }) => item);

const displayEvent = (
  event: DisplayStreamEvent["event"],
  payload: Record<string, unknown>,
  sequence: number,
): DisplayStreamEvent => ({
  event,
  data: {
    source_event_id: `event-${sequence}`,
    run_id: "run-1",
    sequence,
    display_kind: event,
    payload,
    created_at: "2026-06-12T00:00:00Z",
  },
} as DisplayStreamEvent);

describe("replayEvents", () => {
  it("uses the same reducer path as streamed events", () => {
    const events: DisplayStreamEvent[] = [
      displayEvent("assistant_text_delta", { agent_id: "", content: "hello " }, 1),
      displayEvent("assistant_text_delta", { agent_id: "", content: "world" }, 2),
      displayEvent("tool_call", { agent_id: "", call_id: "call-1", tool_name: "bash", arguments: { cmd: "pwd" } }, 3),
      displayEvent("tool_result", { agent_id: "", call_id: "call-1", tool_name: "bash", display_result: "/repo", is_error: false }, 4),
      displayEvent("final_result", { agent_id: "", turn_id: "turn-1", completion_status: "completed" }, 5),
    ];

    const seed: TimelineItem[] = [{ id: "user-1", kind: "user", text: "hi" }];
    const streamed = events.reduce(applyEvent, seed);

    expect(withoutIds(replayEvents(seed, events))).toEqual(withoutIds(streamed));
  });
});
