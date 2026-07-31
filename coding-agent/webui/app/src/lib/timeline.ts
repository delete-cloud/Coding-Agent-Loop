import type {
  ApprovalPromptPayload,
  ApprovalResultPayload,
  AssistantTextPayload,
  DisplayEventEnvelope,
  DisplayStreamEvent,
  ErrorEvent,
  FinalResultPayload,
  ThinkingPayload,
  ToolCallPayload,
  ToolResultPayload,
} from "./types";

export type TimelineItem =
  | { id: string; kind: "user"; text: string }
  | { id: string; kind: "assistant"; agentId: string; text: string }
  | { id: string; kind: "thinking"; agentId: string; text: string }
  | {
      id: string;
      kind: "tool";
      agentId: string;
      callId: string;
      toolName: string;
      args: Record<string, unknown>;
      result?: string;
      isError?: boolean;
      startedAt?: string;
      finishedAt?: string;
    }
  | {
      id: string;
      kind: "approval";
      agentId: string;
      requestId: string;
      toolName: string;
      args: Record<string, unknown>;
      timeoutSeconds?: number;
      promptedAt?: string;
      resolved?: "approved" | "denied";
    }
  | { id: string; kind: "turnEnd"; status: string }
  | { id: string; kind: "error"; text: string };

let seq = 0;
const nextId = () => `i${++seq}`;

const resultText = (d: ToolResultPayload): string => {
  return d.display_result ?? "";
};

export function pushUser(items: TimelineItem[], text: string): TimelineItem[] {
  return [...items, { id: nextId(), kind: "user", text }];
}

export function resolveApproval(
  items: TimelineItem[],
  requestId: string,
  decision: "approved" | "denied",
): TimelineItem[] {
  return items.map((it) =>
    it.kind === "approval" && it.requestId === requestId ? { ...it, resolved: decision } : it,
  );
}

// Fold a streamed DisplayEvent into the timeline (immutably).
export function applyEvent(
  items: TimelineItem[],
  ev: DisplayStreamEvent,
): TimelineItem[] {
  switch (ev.event) {
    case "assistant_text_delta": {
      const envelope = ev.data as DisplayEventEnvelope<AssistantTextPayload>;
      const d = envelope.payload;
      const last = items[items.length - 1];
      if (last?.kind === "assistant" && last.agentId === d.agent_id) {
        const copy = items.slice(0, -1);
        return [...copy, { ...last, text: last.text + (d.content ?? "") }];
      }
      return [
        ...items,
        {
          id: nextId(),
          kind: "assistant",
          agentId: d.agent_id ?? "",
          text: d.content ?? "",
        },
      ];
    }
    case "thinking_delta": {
      const envelope = ev.data as DisplayEventEnvelope<ThinkingPayload>;
      const d = envelope.payload;
      const last = items[items.length - 1];
      if (last?.kind === "thinking" && last.agentId === d.agent_id) {
        const copy = items.slice(0, -1);
        return [...copy, { ...last, text: last.text + (d.text ?? "") }];
      }
      return [
        ...items,
        {
          id: nextId(),
          kind: "thinking",
          agentId: d.agent_id ?? "",
          text: d.text ?? "",
        },
      ];
    }
    case "tool_call": {
      const envelope = ev.data as DisplayEventEnvelope<ToolCallPayload>;
      const d = envelope.payload;
      return [
        ...items,
        {
          id: nextId(),
          kind: "tool",
          agentId: d.agent_id ?? "",
          callId: d.call_id ?? envelope.source_event_id,
          toolName: d.tool_name ?? "",
          args: d.arguments ?? {},
          startedAt: envelope.created_at,
        },
      ];
    }
    case "tool_result": {
      const envelope = ev.data as DisplayEventEnvelope<ToolResultPayload>;
      const d = envelope.payload;
      return items.map((it) =>
        it.kind === "tool" && it.callId === d.call_id
          ? { ...it, result: resultText(d), isError: d.is_error, finishedAt: envelope.created_at }
          : it,
      );
    }
    case "approval_prompt": {
      const envelope = ev.data as DisplayEventEnvelope<ApprovalPromptPayload>;
      const d = envelope.payload;
      return [
        ...items,
        {
          id: nextId(),
          kind: "approval",
          agentId: d.agent_id ?? "",
          requestId: d.request_id ?? envelope.source_event_id,
          toolName: d.tool_call?.tool_name ?? "",
          args: d.tool_call?.arguments ?? {},
          timeoutSeconds: d.timeout_seconds,
          promptedAt: envelope.created_at,
        },
      ];
    }
    case "approval_result": {
      const envelope = ev.data as DisplayEventEnvelope<ApprovalResultPayload>;
      const d = envelope.payload;
      if (!d.request_id) return items;
      if (typeof d.approved !== "boolean") return items;
      return resolveApproval(items, d.request_id, d.approved ? "approved" : "denied");
    }
    case "final_result": {
      const envelope = ev.data as DisplayEventEnvelope<FinalResultPayload>;
      const d = envelope.payload;
      if (d.agent_id) return items; // subagent boundary, ignore
      return [
        ...items,
        {
          id: nextId(),
          kind: "turnEnd",
          status: d.completion_status ?? "completed",
        },
      ];
    }
    case "Error":
    case "ErrorMessage": {
      const d = ev.data as ErrorEvent;
      return [...items, { id: nextId(), kind: "error", text: d.error || d.content || "unknown error" }];
    }
    default:
      return items;
  }
}

export function replayEvents(
  items: TimelineItem[],
  events: DisplayStreamEvent[],
): TimelineItem[] {
  return events.reduce(applyEvent, items);
}

// True when this event closes the root turn (stream should end).
export function isRootTurnEnd(ev: DisplayStreamEvent): boolean {
  const envelope = ev.data as DisplayEventEnvelope<FinalResultPayload>;
  return (
    ev.event === "final_result" &&
    !envelope.payload.agent_id
  );
}

const SUMMARY_KEYS = [
  "path",
  "file_path",
  "filePath",
  "filename",
  "cmd",
  "command",
  "pattern",
  "query",
  "url",
  "goal",
  "prompt",
];

// One-line argument summary for tool cards: prefer well-known path/command
// keys, fall back to the first scalar argument, truncated to one line.
export function toolArgSummary(
  _toolName: string,
  args: Record<string, unknown>,
): string {
  let raw: string | null = null;
  for (const key of SUMMARY_KEYS) {
    const v = args[key];
    if (typeof v === "string" && v.trim()) {
      raw = v;
      break;
    }
  }
  if (raw === null) {
    for (const v of Object.values(args)) {
      if (typeof v === "string" && v.trim()) {
        raw = v;
        break;
      }
      if (typeof v === "number" || typeof v === "boolean") {
        raw = String(v);
        break;
      }
    }
  }
  if (raw === null) return "";
  const oneLine = raw.replace(/\s+/g, " ").trim();
  return oneLine.length > 80 ? `${oneLine.slice(0, 79)}…` : oneLine;
}

// Duration between tool_call and tool_result timestamps, e.g. "1.2s".
// Returns null when either timestamp is missing or unparseable.
export function toolDuration(item: {
  startedAt?: string;
  finishedAt?: string;
}): string | null {
  if (!item.startedAt || !item.finishedAt) return null;
  const ms = new Date(item.finishedAt).getTime() - new Date(item.startedAt).getTime();
  if (!Number.isFinite(ms) || ms < 0) return null;
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.round((ms % 60_000) / 1000);
  return `${m}m${s}s`;
}
