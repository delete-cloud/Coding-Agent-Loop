// User-facing DisplayEvent stream payloads.
// Source of truth: src/coding_agent/events/display.py and http_server.py.

export interface BaseEvent {
  session_id: string;
  agent_id: string; // "" = root agent, non-empty = subagent
  timestamp: string;
}

export interface StreamDelta extends BaseEvent { content: string; role: string }
export interface ThinkingDelta extends BaseEvent { text: string }
export interface TurnStatusDelta extends BaseEvent {
  phase: "thinking" | "streaming" | "tool_call" | "idle" | string;
  elapsed_seconds: number;
  tokens_in: number;
  tokens_out: number;
  model_name: string;
  context_percent: number;
}
export interface ToolCallDelta extends BaseEvent {
  tool_name: string;
  arguments: Record<string, unknown>;
  call_id: string;
}
export interface ToolResultDelta extends BaseEvent {
  call_id: string;
  tool_name: string;
  result?: unknown;
  display_result?: string;
  is_error?: boolean;
}
export interface ToolCall { tool_name: string; arguments: Record<string, unknown>; call_id: string }
export interface ApprovalRequest extends BaseEvent {
  request_id: string;
  tool_call: ToolCall;
  timeout_seconds: number;
}
export interface TurnEnd extends BaseEvent {
  turn_id: string;
  completion_status: "completed" | "blocked" | "error" | string;
}
export interface ErrorEvent extends BaseEvent { content?: string; error?: string }

export type DisplayEventKind =
  | "assistant_text_delta"
  | "thinking_delta"
  | "tool_call"
  | "tool_result"
  | "approval_prompt"
  | "approval_result"
  | "progress_update"
  | "final_result";

export interface DisplayEventEnvelope<Payload extends object = Record<string, unknown>> {
  source_event_id: string;
  run_id: string;
  sequence: number | null;
  display_kind: DisplayEventKind;
  payload: Payload;
  created_at: string;
}

export interface AssistantTextPayload {
  agent_id?: string;
  content?: string;
  role?: string;
}
export interface ThinkingPayload { agent_id?: string; text?: string }
export interface ProgressPayload {
  agent_id?: string;
  phase?: string;
  elapsed_seconds?: number;
  tokens_in?: number;
  tokens_out?: number;
  model_name?: string;
  context_percent?: number;
}
export interface ToolCallPayload {
  agent_id?: string;
  tool_name?: string;
  arguments?: Record<string, unknown>;
  call_id?: string;
}
export interface ToolResultPayload {
  agent_id?: string;
  call_id?: string;
  tool_name?: string;
  display_result?: string;
  is_error?: boolean;
}
export interface ApprovalPromptPayload {
  agent_id?: string;
  request_id?: string;
  tool_call?: ToolCall;
  timeout_seconds?: number;
}
export interface ApprovalResultPayload {
  agent_id?: string;
  request_id?: string;
  approved?: boolean;
  feedback?: string;
}
export interface FinalResultPayload {
  agent_id?: string;
  turn_id?: string;
  completion_status?: "completed" | "blocked" | "error" | string;
}

export type DisplayStreamEvent =
  | { event: "assistant_text_delta"; data: DisplayEventEnvelope<AssistantTextPayload> }
  | { event: "thinking_delta"; data: DisplayEventEnvelope<ThinkingPayload> }
  | { event: "tool_call"; data: DisplayEventEnvelope<ToolCallPayload> }
  | { event: "tool_result"; data: DisplayEventEnvelope<ToolResultPayload> }
  | { event: "approval_prompt"; data: DisplayEventEnvelope<ApprovalPromptPayload> }
  | { event: "approval_result"; data: DisplayEventEnvelope<ApprovalResultPayload> }
  | { event: "progress_update"; data: DisplayEventEnvelope<ProgressPayload> }
  | { event: "final_result"; data: DisplayEventEnvelope<FinalResultPayload> }
  | { event: "Error" | "ErrorMessage"; data: ErrorEvent }
  | { event: string; data: Record<string, unknown> };

export type ApprovalPolicy = "auto" | "interactive" | "yolo";
export type ApprovalScope = "once" | "session";

export interface SessionSummary {
  session_id: string;
  id: string;
  status: "created" | "running" | "waiting_approval" | "completed" | "failed" | "closed";
  turn_status: "idle" | "running" | "cancelling" | "cancelled" | "failed";
  turn_id: string | null;
  created_at: string;
  updated_at: string;
  last_activity: string;
  turn_in_progress: boolean;
  pending_approval: boolean;
  provider_name: string | null;
  model_name: string | null;
  base_url: string | null;
  max_steps: number;
  origin: Record<string, string> | null;
  default_run_target: Record<string, unknown>;
  workspace_id: string | null;
  resumable: boolean;
  last_run_id: string | null;
  last_run_status: string | null;
  last_interrupted_run_id: string | null;
  resume_from_event_id: string | null;
  checkpoint_count: number;
  latest_checkpoint_id: string | null;
  latest_checkpoint_label: string | null;
}

export interface RuntimeRun {
  run_id: string;
  session_id: string;
  tape_id: string | null;
  parent_run_id: string | null;
  agent_id: string | null;
  status: string;
  started_at: string;
  ended_at: string | null;
  metadata: Record<string, unknown>;
  result: Record<string, unknown>;
  error: string | null;
}

export interface MemoryReviewRecord {
  candidate_id: string;
  status: "candidate" | "accepted" | "rejected" | "archived" | string;
  review_reason?: string | null;
  kind: string;
  title: string;
  summary: string;
  scope: string;
  tags: string[];
  confidence: number;
  topic_id?: string | null;
  session_id?: string | null;
  tape_id?: string | null;
}

export interface ContextPackItem {
  source_kind: string;
  source_id: string;
  label: string;
  body?: string | null;
  score?: number | null;
  score_scale?: "similarity" | "overlap" | string | null;
  rank?: number | null;
  evidence?: unknown;
  metadata?: Record<string, unknown> | null;
}

export interface ContextPackSection {
  title?: string;
  items?: ContextPackItem[];
}

export interface ContextPack {
  title?: string;
  sections?: ContextPackSection[];
}

export interface SessionResult {
  session_id: string;
  status: string;
  final_answer: string | null;
  verification_summary: string | null;
  failure_details: string | null;
}

export interface DiffFile {
  path: string;
  status: "added" | "modified" | "deleted" | "renamed" | "binary" | "unknown";
  old_path?: string | null;
  additions?: number | null;
  deletions?: number | null;
  binary?: boolean;
}
export interface WorkspaceDiff {
  session_id: string;
  workspace_id?: string | null;
  files: DiffFile[];
  additions: number;
  deletions: number;
}

export interface WorkspacePatch {
  session_id: string;
  workspace_id?: string | null;
  format: "unified_diff";
  patch: string;
}

// GET /providers/{provider}/models — ids are normalized to plain strings by
// the client; source="unavailable" means the provider could not be queried.
export interface ProviderModels {
  provider: string;
  models: string[];
  source: "live" | "unavailable";
}
