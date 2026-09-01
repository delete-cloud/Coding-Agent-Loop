import { describe, expect, it } from "vitest";

import fixture from "../../../test/fixtures/connected-chat/v1/connected-chat-contract.json";

import {
  CONNECTED_CHAT_CONTRACT_VERSION,
  ContractViolationError,
  parseApiError,
  parseChatEvent,
  parseChatSnapshot,
  parseStreamControl,
} from "./wire";

// ---------------------------------------------------------------------------
// Test-only cursor codec. The contract declares cursors opaque to clients, so
// production code never constructs or edits them; these helpers exist only to
// prove byte-for-byte parity with the documented Python canonical algorithm.
// ---------------------------------------------------------------------------

function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const record = value as Record<string, unknown>;
  const keys = Object.keys(record).sort();
  const parts = keys.map((k) => `${JSON.stringify(k)}:${canonicalJson(record[k])}`);
  return `{${parts.join(",")}}`;
}

function encodeCursor(payload: Record<string, unknown>): string {
  const raw = new TextEncoder().encode(canonicalJson(payload));
  let binary = "";
  for (const byte of raw) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function decodeCursor(encoded: string): Record<string, unknown> {
  const padded = encoded + "=".repeat((4 - (encoded.length % 4)) % 4);
  const binary = atob(padded.replaceAll("-", "+").replaceAll("_", "/"));
  const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
  return JSON.parse(new TextDecoder().decode(bytes)) as Record<string, unknown>;
}

function fixtureCursors(): string[] {
  const cursors: string[] = [];
  for (const example of fixture.cursor.examples) cursors.push(example.encoded);
  cursors.push(fixture.http.snapshot.response.snapshot_cursor);
  const nextCursor = fixture.http.snapshot.response.next_cursor;
  if (nextCursor !== null) cursors.push(nextCursor);
  cursors.push(fixture.http.follow.cursor);
  for (const control of fixture.stream_controls) cursors.push(control.data.cursor);
  return cursors;
}

const EVENTS = fixture.events;
const eventsByKind = (kind: string) => EVENTS.filter((e) => e.data.kind === kind);

describe("fixture identity", () => {
  it("pins contract id, version, and revision", () => {
    expect(fixture.contract_id).toBe("cal.connected-chat");
    expect(fixture.contract_version).toBe(CONNECTED_CHAT_CONTRACT_VERSION);
    expect(fixture.fixture_revision).toBe("2026-08-24.r3");
    expect(fixture.projection).toEqual({ name: "connected-chat", epoch: "7" });
  });

  it("decodes every fixture cursor and re-encodes byte-for-byte", () => {
    const cursors = fixtureCursors();
    expect(cursors.length).toBeGreaterThanOrEqual(7);
    for (const encoded of cursors) {
      const payload = decodeCursor(encoded);
      expect(payload.v).toBe(1);
      expect(payload.kind).toBe("chat");
      expect(payload.projection).toBe("connected-chat");
      expect(encodeCursor(payload)).toBe(encoded);
    }
  });

  it("canonical encoding matches the documented sorted compact JSON algorithm", () => {
    const example = fixture.cursor.examples[2];
    expect(encodeCursor(example.payload as Record<string, unknown>)).toBe(example.encoded);
  });
});

describe("parseChatEvent", () => {
  it("parses all eight event kinds from the fixture", () => {
    const kinds = [
      "user_prompt",
      "thinking",
      "progress",
      "tool_call",
      "tool_result",
      "assistant_message",
      "approval_requested",
      "root_terminal",
    ];
    for (const kind of kinds) {
      const frames = eventsByKind(kind);
      expect(frames.length).toBeGreaterThanOrEqual(1);
      for (const frame of frames) {
        const event = parseChatEvent(frame.data);
        expect(event.kind).toBe(kind);
        expect(event.contract_version).toBe(CONNECTED_CHAT_CONTRACT_VERSION);
        expect(event.source_event_id).toBe(frame.data.source_event_id);
        // Decimal-string sequence: preserved verbatim, never number-coerced.
        expect(event.session_seq).toBe(frame.data.session_seq);
        expect(typeof event.session_seq).toBe("string");
        expect(event.session_id).toBe("session-01");
        expect(event.created_at).toBe(frame.data.created_at);
      }
    }
  });

  it("parses all four terminal outcomes", () => {
    const outcomes = eventsByKind("root_terminal").map((frame) => {
      const event = parseChatEvent(frame.data);
      if (event.kind !== "root_terminal") throw new Error("wrong kind");
      return event.payload.outcome;
    });
    expect(outcomes).toEqual(["completed", "failed", "cancelled", "interrupted"]);
  });

  it("parses tool payloads with typed fields", () => {
    const call = parseChatEvent(eventsByKind("tool_call")[0].data);
    if (call.kind !== "tool_call") throw new Error("wrong kind");
    expect(call.payload.call_id).toBe("call-01");
    expect(call.payload.tool_name).toBe("bash");
    expect(call.payload.arguments).toEqual({ command: "pytest" });

    const result = parseChatEvent(eventsByKind("tool_result")[0].data);
    if (result.kind !== "tool_result") throw new Error("wrong kind");
    expect(result.payload.call_id).toBe("call-01");
    expect(result.payload.output).toBe("42 passed");
    expect(result.payload.is_error).toBe(false);
  });
  it("parses an exact child approval payload on the parent envelope", () => {
    const approval = parseChatEvent(
      eventsByKind("approval_requested")[0].data,
    );
    if (approval.kind !== "approval_requested") {
      throw new Error("wrong kind");
    }
    expect(approval.run_id).toBe("run-01");
    expect(approval.payload).toEqual({
      approval_request_id: "approval-01",
      tool_call_id: "call-child-01",
      tool_name: "write_file",
      arguments: { path: "src/example.py" },
      effect_id: "effect-child-01",
      attempt_id: "attempt-child-01",
      target_run_id:
        "session-01:run-01:child:effect-child-01:attempt-child-01",
      target_parent_effect_id: "effect-child-01",
    });
  });

  it("rejects additive fields on approval payloads", () => {
    const base = eventsByKind("approval_requested")[0].data;
    expect(() =>
      parseChatEvent({
        ...base,
        payload: { ...base.payload, action_url: "/unsafe" },
      }),
    ).toThrow(ContractViolationError);
  });


  it("preserves unknown additive payload fields", () => {
    const base = eventsByKind("user_prompt")[0].data;
    const event = parseChatEvent({ ...base, payload: { ...base.payload, future_field: 1 } });
    if (event.kind !== "user_prompt") throw new Error("wrong kind");
    expect(event.payload.text).toBe("Run tests");
    expect(event.payload.future_field).toBe(1);
  });

  it("rejects unknown event kinds instead of dropping them", () => {
    const base = eventsByKind("user_prompt")[0].data;
    expect(() => parseChatEvent({ ...base, kind: "approval_prompt" })).toThrow(
      ContractViolationError,
    );
  });

  it("rejects a contract version mismatch", () => {
    const base = eventsByKind("user_prompt")[0].data;
    expect(() => parseChatEvent({ ...base, contract_version: "2.0.0" })).toThrow(
      /contract_version/,
    );
  });

  it("fails fast on missing required fields", () => {
    const base = eventsByKind("user_prompt")[0].data;
    for (const field of [
      "contract_version",
      "source_event_id",
      "session_seq",
      "session_id",
      "run_id",
      "kind",
      "created_at",
      "payload",
    ]) {
      const broken: Record<string, unknown> = { ...base };
      delete broken[field];
      expect(() => parseChatEvent(broken), field).toThrow(ContractViolationError);
    }
  });

  it("rejects non-decimal-string session_seq values", () => {
    const base = eventsByKind("user_prompt")[0].data;
    for (const bad of [12, "12.5", "0x12", "abc", "", " 12"]) {
      expect(() => parseChatEvent({ ...base, session_seq: bad }), String(bad)).toThrow(
        ContractViolationError,
      );
    }
  });

  it("rejects malformed typed payloads", () => {
    const call = eventsByKind("tool_call")[0].data;
    expect(() =>
      parseChatEvent({ ...call, payload: { ...call.payload, call_id: 7 } }),
    ).toThrow(ContractViolationError);

    const terminal = eventsByKind("root_terminal")[0].data;
    expect(() =>
      parseChatEvent({ ...terminal, payload: { ...terminal.payload, outcome: "exploded" } }),
    ).toThrow(ContractViolationError);
  });
});

describe("parseChatSnapshot", () => {
  it("parses the fixture snapshot envelope", () => {
    const snapshot = parseChatSnapshot(fixture.http.snapshot.response);
    expect(snapshot.contract_version).toBe(CONNECTED_CHAT_CONTRACT_VERSION);
    expect(snapshot.session_id).toBe("session-01");
    expect(snapshot.projection).toBe("connected-chat");
    expect(snapshot.projection_epoch).toBe("7");
    expect(snapshot.snapshot_cursor).toBe(fixture.http.snapshot.response.snapshot_cursor);
    expect(snapshot.next_cursor).toBe(fixture.http.snapshot.response.next_cursor);
    expect(snapshot.events).toEqual([]);
  });

  it("accepts a null next_cursor at the high-water mark", () => {
    const snapshot = parseChatSnapshot({
      ...fixture.http.snapshot.response,
      next_cursor: null,
    });
    expect(snapshot.next_cursor).toBeNull();
  });

  it("parses snapshot events through the same event parser", () => {
    const snapshot = parseChatSnapshot({
      ...fixture.http.snapshot.response,
      events: [EVENTS[0].data, EVENTS[5].data],
    });
    expect(snapshot.events).toHaveLength(2);
    expect(snapshot.events[0].kind).toBe("user_prompt");
    expect(snapshot.events[1].kind).toBe("assistant_message");
  });

  it("fails fast when snapshot required fields are missing", () => {
    for (const field of ["contract_version", "session_id", "snapshot_cursor", "events"]) {
      const broken: Record<string, unknown> = { ...fixture.http.snapshot.response };
      delete broken[field];
      expect(() => parseChatSnapshot(broken), field).toThrow(ContractViolationError);
    }
  });

  it("rejects events that belong to another session", () => {
    const foreign = { ...EVENTS[0].data, session_id: "session-other" };
    expect(() =>
      parseChatSnapshot({
        ...fixture.http.snapshot.response,
        events: [foreign],
      }),
    ).toThrow(ContractViolationError);
  });

});

describe("parseApiError", () => {
  it("parses named admission errors", () => {
    const codes = fixture.http.errors.admission.map((entry) => {
      const error = parseApiError(entry.body);
      expect(error.message).toBe(entry.body.error.message);
      expect(error.retryable).toBe(entry.body.error.retryable);
      return error.code;
    });
    expect(codes).toEqual(["turn_in_progress", "command_conflict", "prompt_required"]);
  });

  it("parses named lifecycle errors", () => {
    const codes = fixture.http.errors.lifecycle.map((entry) => parseApiError(entry.body).code);
    expect(codes).toEqual(["resume_source_unsettled", "no_active_turn"]);
  });

  it("parses the named auth error", () => {
    const error = parseApiError(fixture.http.errors.auth[0].body);
    expect(error.code).toBe("credentials_required");
    expect(error.retryable).toBe(false);
  });

  it("parses replay_required when present and omits it when absent", () => {
    const withReplay = parseApiError({
      error: { code: "cursor_expired", message: "expired", retryable: false, replay_required: true },
    });
    expect(withReplay.replay_required).toBe(true);
    const without = parseApiError(fixture.http.errors.auth[0].body);
    expect(without.replay_required).toBeUndefined();
  });

  it("rejects bodies without the error envelope", () => {
    expect(() => parseApiError({})).toThrow(ContractViolationError);
    expect(() => parseApiError({ error: { code: "x" } })).toThrow(ContractViolationError);
  });

  it("fixture enumerates all five cursor error cases with replay semantics", () => {
    expect(fixture.cursor.errors).toEqual([
      { case: "malformed", status: 400, reason: "cursor_malformed", replay_required: false },
      { case: "foreign", status: 409, reason: "cursor_foreign_session", replay_required: false },
      { case: "expired", status: 410, reason: "cursor_expired", replay_required: true },
      { case: "wrong_epoch", status: 409, reason: "cursor_wrong_epoch", replay_required: true },
      { case: "future", status: 409, reason: "cursor_future", replay_required: false },
    ]);
  });
});

describe("parseStreamControl", () => {
  it("parses the three exact stream-control reasons with opaque cursors", () => {
    const reasons = fixture.stream_controls.map((frame) => {
      const control = parseStreamControl(frame.data);
      expect(control.kind).toBe("replay_required");
      expect(control.contract_version).toBe(CONNECTED_CHAT_CONTRACT_VERSION);
      // Cursor stays opaque: stored verbatim, never decoded by the client.
      expect(control.cursor).toBe(frame.data.cursor);
      return control.reason;
    });
    expect(reasons).toEqual(["subscriber_queue_overflow", "ownership_lost", "sequence_loss"]);
  });

  it("rejects unknown control kinds and reasons", () => {
    const base = fixture.stream_controls[0].data;
    expect(() => parseStreamControl({ ...base, kind: "resume_hint" })).toThrow(
      ContractViolationError,
    );
    expect(() => parseStreamControl({ ...base, reason: "bored" })).toThrow(
      ContractViolationError,
    );
  });
});
