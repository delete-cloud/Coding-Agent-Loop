// Pure stable-ID timeline reducer.
//
// All delivery paths (snapshot pages, owning POST streams, passive follow
// streams) fold through reduceChatEvent. Identity is the stable logical
// source_event_id: duplicates from at-least-once delivery and replay overlap
// never create duplicate nodes. Ordering is by the decimal-string session_seq
// compared as a string (length, then lexicographic) — never Number-coerced.
// Tool results correlate to calls by call_id independently of arrival order:
// a result that arrives before its call is retained in pendingToolResults and
// merged when the call shows up.

import type { ChatEventEnvelope } from "./wire";

export interface TimelineNode {
  /** The primary event: the node itself (user prompt, call, terminal, ...). */
  event: ChatEventEnvelope;
  /** For tool_call nodes, the merged tool_result once observed. */
  result: ChatEventEnvelope | null;
}

export interface TimelineState {
  /** source_event_ids ordered by decimal-string session_seq. */
  order: string[];
  /** Nodes keyed by primary source_event_id. */
  byId: Map<string, TimelineNode>;
  /** Results awaiting their call, keyed by `${run_id}:${call_id}`. */
  pendingToolResults: Map<string, ChatEventEnvelope>;
}

export function createTimelineState(): TimelineState {
  return { order: [], byId: new Map(), pendingToolResults: new Map() };
}

/**
 * Compare two contract-validated decimal-string integers without Number
 * conversion: longer string is larger; equal length is lexicographic.
 */
export function compareDecimalStrings(a: string, b: string): number {
  if (a.length !== b.length) return a.length - b.length;
  if (a === b) return 0;
  return a < b ? -1 : 1;
}

function insertOrder(order: string[], byId: Map<string, TimelineNode>, event: ChatEventEnvelope) {
  // Appends dominate; scan from the end. Ties (equal seq) fall back to
  // source_event_id for determinism.
  const next = [...order];
  let i = next.length;
  while (i > 0) {
    const other = byId.get(next[i - 1]);
    if (!other) throw new Error(`timeline invariant violated: ${next[i - 1]} missing from byId`);
    const seqCmp = compareDecimalStrings(event.session_seq, other.event.session_seq);
    if (seqCmp > 0) break;
    if (seqCmp === 0 && event.source_event_id > other.event.source_event_id) break;
    i -= 1;
  }
  next.splice(i, 0, event.source_event_id);
  return next;
}

function toolKey(runId: string | null, callId: string): string {
  return `${runId ?? ""}:${callId}`;
}

function findCallNode(
  byId: Map<string, TimelineNode>,
  runId: string | null,
  callId: string,
): TimelineNode | null {
  for (const node of byId.values()) {
    if (
      node.event.kind === "tool_call"
      && node.event.run_id === runId
      && node.event.payload.call_id === callId
    ) {
      return node;
    }
  }
  return null;
}

export function reduceChatEvent(state: TimelineState, event: ChatEventEnvelope): TimelineState {
  // Exact redelivery of a primary node is a no-op.
  if (state.byId.has(event.source_event_id)) return state;

  if (event.kind === "tool_result") {
    const key = toolKey(event.run_id, event.payload.call_id);
    const call = findCallNode(state.byId, event.run_id, event.payload.call_id);
    if (call) {
      // First result wins; a repeat of the same or a conflicting result does
      // not create a node and does not replace the merged one.
      if (call.result !== null) return state;
      const byId = new Map(state.byId);
      byId.set(call.event.source_event_id, { event: call.event, result: event });
      return { order: state.order, byId, pendingToolResults: state.pendingToolResults };
    }
    if (state.pendingToolResults.has(key)) return state; // first pending wins
    const pendingToolResults = new Map(state.pendingToolResults);
    pendingToolResults.set(key, event);
    return { order: state.order, byId: state.byId, pendingToolResults };
  }

  // Every other kind becomes its own node. A tool_call adopts a pending
  // result that arrived before it.
  let result: ChatEventEnvelope | null = null;
  let pendingToolResults = state.pendingToolResults;
  if (event.kind === "tool_call") {
    const key = toolKey(event.run_id, event.payload.call_id);
    const pending = state.pendingToolResults.get(key);
    if (pending) {
      result = pending;
      pendingToolResults = new Map(state.pendingToolResults);
      pendingToolResults.delete(key);
    }
  }

  const byId = new Map(state.byId);
  byId.set(event.source_event_id, { event, result });
  return {
    order: insertOrder(state.order, byId, event),
    byId,
    pendingToolResults,
  };
}
