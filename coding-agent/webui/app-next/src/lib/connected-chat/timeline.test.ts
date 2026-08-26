import { describe, expect, it } from "vitest";

import fixture from "../../../test/fixtures/connected-chat/v1/connected-chat-contract.json";

import {
  createTimelineState,
  reduceChatEvent,
  type TimelineState,
} from "./timeline";
import { parseChatEvent, type ChatEventEnvelope } from "./wire";

function envelope(overrides: Record<string, unknown>): ChatEventEnvelope {
  return parseChatEvent({
    contract_version: fixture.contract_version,
    source_event_id: "evt-x",
    session_seq: "1",
    session_id: "session-01",
    run_id: "run-01",
    kind: "thinking",
    created_at: "2026-08-24T00:00:00Z",
    payload: { text: "..." },
    ...overrides,
  });
}

function reduceAll(state: TimelineState, events: ChatEventEnvelope[]): TimelineState {
  return events.reduce(reduceChatEvent, state);
}

const fixtureEvents = fixture.events.map((frame) => parseChatEvent(frame.data));
const byId = (id: string) => {
  const found = fixtureEvents.find((event) => event.source_event_id === id);
  if (!found) throw new Error(`fixture missing ${id}`);
  return found;
};

describe("reduceChatEvent", () => {
  it("dedupes identical events delivered by snapshot, POST, and follow", () => {
    const userPrompt = byId("evt-user-01");
    const state = reduceAll(createTimelineState(), [userPrompt, userPrompt, userPrompt]);

    expect(state.order).toEqual(["evt-user-01"]);
    expect(state.byId.size).toBe(1);
    const node = state.byId.get("evt-user-01");
    expect(node?.event.kind).toBe("user_prompt");
  });

  it("dedupes the fixture replay/queued overlap example", () => {
    const replay = fixture.overlap_example.replay_source_event_ids.map(byId);
    const queued = fixture.overlap_example.queued_source_event_ids.map(byId);
    // The tool call precedes the overlap so the replayed result can merge.
    const state = reduceAll(createTimelineState(), [byId("evt-tool-call-01"), ...replay, ...queued]);

    // tool_result merged into its call node, not duplicated; assistant once.
    expect(state.order).toEqual([
      "evt-tool-call-01",
      "evt-assistant-01",
      "evt-terminal-completed",
    ]);
    expect(state.byId.size).toBe(3);
    expect(state.byId.get("evt-tool-call-01")?.result?.source_event_id).toBe(
      "evt-tool-result-01",
    );
    expect(state.pendingToolResults.size).toBe(0);
  });

  it("orders nodes by decimal-string session_seq without number coercion", () => {
    // Lexicographic order would place "10" before "9"; decimal-string compare
    // must not. Delivered deliberately out of arrival order.
    const seqs = ["10", "2", "9", "100", "20"];
    const events = seqs.map((seq, index) =>
      envelope({ source_event_id: `evt-${seq}`, session_seq: seq, created_at: `2026-08-24T00:00:0${index}Z` }),
    );
    const state = reduceAll(createTimelineState(), events);

    expect(state.order).toEqual(["evt-2", "evt-9", "evt-10", "evt-20", "evt-100"]);
  });

  it("merges a tool result that arrives after its call", () => {
    const state = reduceAll(createTimelineState(), [
      byId("evt-tool-call-01"),
      byId("evt-tool-result-01"),
    ]);

    expect(state.order).toEqual(["evt-tool-call-01"]);
    const node = state.byId.get("evt-tool-call-01");
    expect(node?.result?.source_event_id).toBe("evt-tool-result-01");
    expect(node?.result?.kind).toBe("tool_result");
    expect(state.pendingToolResults.size).toBe(0);
  });

  it("retains a result-before-call as pending and merges when the call arrives", () => {
    let state = reduceChatEvent(createTimelineState(), byId("evt-tool-result-01"));

    expect(state.byId.size).toBe(0);
    expect(state.order).toEqual([]);
    expect(state.pendingToolResults.get("run-01:call-01")?.source_event_id).toBe("evt-tool-result-01");

    state = reduceChatEvent(state, byId("evt-tool-call-01"));
    expect(state.order).toEqual(["evt-tool-call-01"]);
    expect(state.byId.get("evt-tool-call-01")?.result?.source_event_id).toBe(
      "evt-tool-result-01",
    );
    expect(state.pendingToolResults.size).toBe(0);
  });

  it("ignores duplicate tool calls and duplicate tool results", () => {
    const once = reduceAll(createTimelineState(), [
      byId("evt-tool-call-01"),
      byId("evt-tool-result-01"),
    ]);
    const twice = reduceAll(once, [byId("evt-tool-call-01"), byId("evt-tool-result-01")]);

    expect(twice.order).toEqual(["evt-tool-call-01"]);
    expect(twice.byId.size).toBe(1);
    expect(twice.byId.get("evt-tool-call-01")?.result?.source_event_id).toBe(
      "evt-tool-result-01",
    );
  });

  it("keeps a result pending when its call never arrives", () => {
    const orphan = envelope({
      source_event_id: "evt-orphan-result",
      session_seq: "40",
      kind: "tool_result",
      payload: { call_id: "call-never", output: "x", is_error: false },
    });
    const state = reduceChatEvent(createTimelineState(), orphan);

    expect(state.order).toEqual([]);
    expect(state.pendingToolResults.get("run-01:call-never")?.source_event_id).toBe(
      "evt-orphan-result",
    );
  });

  it("produces exactly one root terminal node per terminal event", () => {
    const terminal = byId("evt-terminal-completed");
    const state = reduceAll(createTimelineState(), [
      byId("evt-user-01"),
      terminal,
      terminal, // duplicate delivery
    ]);

    expect(state.order).toEqual(["evt-user-01", "evt-terminal-completed"]);
    const node = state.byId.get("evt-terminal-completed");
    expect(node?.event.kind).toBe("root_terminal");
    if (node?.event.kind !== "root_terminal") throw new Error("unreachable");
    expect(node.event.payload.outcome).toBe("completed");
  });

  it("is pure: inputs are not mutated and duplicates return the same state", () => {
    const first = byId("evt-user-01");
    const empty = createTimelineState();
    const one = reduceChatEvent(empty, first);

    expect(empty.order).toEqual([]);
    expect(empty.byId.size).toBe(0);

    const again = reduceChatEvent(one, first);
    expect(again).toBe(one);

    const grown = reduceChatEvent(one, byId("evt-thinking-01"));
    expect(grown).not.toBe(one);
    expect(one.order).toEqual(["evt-user-01"]);
    expect(grown.order).toEqual(["evt-user-01", "evt-thinking-01"]);
  });
});
