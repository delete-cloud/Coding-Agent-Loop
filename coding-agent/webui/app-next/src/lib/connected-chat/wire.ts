// Wire boundary for the connected-chat v1 contract.
//
// Every byte of JSON that enters the app — snapshot bodies, SSE event data,
// stream controls, and checked error bodies — is validated here exactly once.
// Parsers return exact domain values or throw ContractViolationError; there
// are no fallback defaults for required fields. Unknown additive payload
// fields are preserved verbatim (the contract allows them); unknown event
// kinds and stream-control reasons are rejected, never dropped.
//
// Cursors are typed opaque strings here on purpose: the client stores and
// transmits them but never decodes, constructs, or edits them. The canonical
// cursor codec is exercised byte-for-byte in wire.test.ts only.

export const CONNECTED_CHAT_CONTRACT_VERSION = "1.1.0";

export class ContractViolationError extends Error {
  readonly path: string;

  constructor(path: string, message: string) {
    super(`${path}: ${message}`);
    this.name = "ContractViolationError";
    this.path = path;
  }
}

// ---------------------------------------------------------------------------
// Scalar guards. Each throws ContractViolationError with a dotted path.
// ---------------------------------------------------------------------------

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function reqRecord(value: unknown, path: string): Record<string, unknown> {
  if (!isRecord(value)) throw new ContractViolationError(path, "expected an object");
  return value;
}

function reqString(value: unknown, path: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new ContractViolationError(path, "expected a non-empty string");
  }
  return value;
}

/** Like reqString but permits the empty string (e.g. empty tool output). */
function reqText(value: unknown, path: string): string {
  if (typeof value !== "string") throw new ContractViolationError(path, "expected a string");
  return value;
}

function reqBoolean(value: unknown, path: string): boolean {
  if (typeof value !== "boolean") throw new ContractViolationError(path, "expected a boolean");
  return value;
}

function reqNumber(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new ContractViolationError(path, "expected a finite number");
  }
  return value;
}

function reqNullableString(value: unknown, path: string): string | null {
  if (value === null) return null;
  return reqString(value, path);
}

const DECIMAL_STRING = /^(0|[1-9]\d*)$/;

function reqDecimalString(value: unknown, path: string): string {
  const text = reqString(value, path);
  if (!DECIMAL_STRING.test(text)) {
    throw new ContractViolationError(path, "expected a decimal-string integer");
  }
  return text;
}

const RFC3339 =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/;

function reqRfc3339(value: unknown, path: string): string {
  const text = reqString(value, path);
  if (!RFC3339.test(text) || Number.isNaN(Date.parse(text))) {
    throw new ContractViolationError(path, "expected an RFC3339 timestamp");
  }
  return text;
}

function reqContractVersion(value: unknown, path: string): string {
  const version = reqString(value, path);
  if (version !== CONNECTED_CHAT_CONTRACT_VERSION) {
    throw new ContractViolationError(
      path,
      `unsupported contract_version ${JSON.stringify(version)}; expected ${CONNECTED_CHAT_CONTRACT_VERSION}`,
    );
  }
  return version;
}

// ---------------------------------------------------------------------------
// Chat event envelopes
// ---------------------------------------------------------------------------

export interface UserPromptPayload {
  text: string;
  [extra: string]: unknown;
}

export interface AssistantMessagePayload {
  text: string;
  [extra: string]: unknown;
}

export interface ThinkingPayload {
  text: string;
  [extra: string]: unknown;
}

export interface ProgressPayload {
  current: number;
  total: number;
  label: string;
  [extra: string]: unknown;
}

export interface ToolCallPayload {
  call_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  [extra: string]: unknown;
}

export interface ToolResultPayload {
  call_id: string;
  output: string;
  is_error: boolean;
  [extra: string]: unknown;
}
export interface ApprovalRequestedPayload {
  approval_request_id: string;
  tool_call_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  effect_id: string;
  attempt_id: string;
  target_run_id?: string;
  target_parent_effect_id?: string;
}


export interface TerminalError {
  code: string;
  message: string;
  [extra: string]: unknown;
}

export type TerminalOutcome = "completed" | "failed" | "cancelled" | "interrupted";

export interface RootTerminalPayload {
  outcome: TerminalOutcome;
  result: string | null;
  error: TerminalError | null;
  [extra: string]: unknown;
}

export type ChatEventPayload =
  | UserPromptPayload
  | AssistantMessagePayload
  | ThinkingPayload
  | ProgressPayload
  | ToolCallPayload
  | ToolResultPayload
  | ApprovalRequestedPayload
  | RootTerminalPayload;

interface EnvelopeBase {
  contract_version: string;
  source_event_id: string;
  /** Decimal-string sequence; never number-coerced. */
  session_seq: string;
  session_id: string;
  run_id: string | null;
  created_at: string;
}

export type ChatEventEnvelope =
  | (EnvelopeBase & { kind: "user_prompt"; payload: UserPromptPayload })
  | (EnvelopeBase & { kind: "assistant_message"; payload: AssistantMessagePayload })
  | (EnvelopeBase & { kind: "thinking"; payload: ThinkingPayload })
  | (EnvelopeBase & { kind: "progress"; payload: ProgressPayload })
  | (EnvelopeBase & { kind: "tool_call"; payload: ToolCallPayload })
  | (EnvelopeBase & { kind: "tool_result"; payload: ToolResultPayload })
  | (EnvelopeBase & {
      kind: "approval_requested";
      payload: ApprovalRequestedPayload;
    })
  | (EnvelopeBase & { kind: "root_terminal"; payload: RootTerminalPayload });

export type ChatEventKind = ChatEventEnvelope["kind"];

const TERMINAL_OUTCOMES: ReadonlySet<string> = new Set([
  "completed",
  "failed",
  "cancelled",
  "interrupted",
]);

function parseTerminalError(value: unknown, path: string): TerminalError | null {
  if (value === null) return null;
  const record = reqRecord(value, path);
  return {
    ...record,
    code: reqString(record.code, `${path}.code`),
    message: reqString(record.message, `${path}.message`),
  };
}

function parsePayload(kind: string, value: unknown): ChatEventPayload {
  const path = `payload`;
  const record = reqRecord(value, path);
  switch (kind) {
    case "user_prompt":
    case "assistant_message":
    case "thinking":
      return { ...record, text: reqString(record.text, `${path}.text`) };
    case "progress":
      return {
        ...record,
        current: reqNumber(record.current, `${path}.current`),
        total: reqNumber(record.total, `${path}.total`),
        label: reqString(record.label, `${path}.label`),
      };
    case "tool_call":
      return {
        ...record,
        call_id: reqString(record.call_id, `${path}.call_id`),
        tool_name: reqString(record.tool_name, `${path}.tool_name`),
        arguments: reqRecord(record.arguments, `${path}.arguments`),
      };
    case "tool_result":
      return {
        ...record,
        call_id: reqString(record.call_id, `${path}.call_id`),
        output: typeof record.output === "string" ? record.output : reqString(record.output, `${path}.output`),
        is_error: reqBoolean(record.is_error, `${path}.is_error`),
      };
    case "approval_requested": {
      const allowed = new Set([
        "approval_request_id",
        "tool_call_id",
        "tool_name",
        "arguments",
        "effect_id",
        "attempt_id",
        "target_run_id",
        "target_parent_effect_id",
      ]);
      for (const key of Object.keys(record)) {
        if (!allowed.has(key)) {
          throw new ContractViolationError(
            `${path}.${key}`,
            "unknown approval_requested payload field",
          );
        }
      }
      const targetRunId =
        record.target_run_id === undefined
          ? undefined
          : reqString(record.target_run_id, `${path}.target_run_id`);
      const targetParentEffectId =
        record.target_parent_effect_id === undefined
          ? undefined
          : reqString(
              record.target_parent_effect_id,
              `${path}.target_parent_effect_id`,
            );
      if ((targetRunId === undefined) !== (targetParentEffectId === undefined)) {
        throw new ContractViolationError(
          path,
          "child approval targets must appear together",
        );
      }
      return {
        approval_request_id: reqString(
          record.approval_request_id,
          `${path}.approval_request_id`,
        ),
        tool_call_id: reqString(record.tool_call_id, `${path}.tool_call_id`),
        tool_name: reqString(record.tool_name, `${path}.tool_name`),
        arguments: reqRecord(record.arguments, `${path}.arguments`),
        effect_id: reqString(record.effect_id, `${path}.effect_id`),
        attempt_id: reqString(record.attempt_id, `${path}.attempt_id`),
        ...(targetRunId === undefined
          ? {}
          : {
              target_run_id: targetRunId,
              target_parent_effect_id: targetParentEffectId,
            }),
      };
    }
    case "root_terminal": {
      const outcome = reqString(record.outcome, `${path}.outcome`);
      if (!TERMINAL_OUTCOMES.has(outcome)) {
        throw new ContractViolationError(
          `${path}.outcome`,
          `unknown terminal outcome ${JSON.stringify(outcome)}`,
        );
      }
      return {
        ...record,
        outcome: outcome as TerminalOutcome,
        result: record.result === null ? null : reqString(record.result, `${path}.result`),
        error: parseTerminalError(record.error, `${path}.error`),
      };
    }
    default:
      throw new ContractViolationError("kind", `unknown event kind ${JSON.stringify(kind)}`);
  }
}

export function parseChatEvent(value: unknown): ChatEventEnvelope {
  const record = reqRecord(value, "event");
  const base: EnvelopeBase = {
    contract_version: reqContractVersion(record.contract_version, "event.contract_version"),
    source_event_id: reqString(record.source_event_id, "event.source_event_id"),
    session_seq: reqDecimalString(record.session_seq, "event.session_seq"),
    session_id: reqString(record.session_id, "event.session_id"),
    run_id: reqNullableString(record.run_id, "event.run_id"),
    created_at: reqRfc3339(record.created_at, "event.created_at"),
  };
  const kind = reqString(record.kind, "event.kind");
  const payload = parsePayload(kind, record.payload);
  return { ...base, kind, payload } as ChatEventEnvelope;
}

// ---------------------------------------------------------------------------
// Snapshot envelope
// ---------------------------------------------------------------------------

export interface ChatSnapshot {
  contract_version: string;
  session_id: string;
  projection: string;
  projection_epoch: string;
  snapshot_cursor: string;
  next_cursor: string | null;
  events: ChatEventEnvelope[];
}

export function parseChatSnapshot(value: unknown): ChatSnapshot {
  const record = reqRecord(value, "snapshot");
  if (!Array.isArray(record.events)) {
    throw new ContractViolationError("snapshot.events", "expected an array");
  }
  const session_id = reqString(record.session_id, "snapshot.session_id");
  const events = record.events.map((event, index) => {
    const parsed = parseChatEvent(event);
    if (parsed.session_id !== session_id) {
      throw new ContractViolationError(
        `snapshot.events[${index}].session_id`,
        `expected ${session_id}`,
      );
    }
    return parsed;
  });
  return {
    contract_version: reqContractVersion(record.contract_version, "snapshot.contract_version"),
    session_id,
    projection: reqString(record.projection, "snapshot.projection"),
    projection_epoch: reqDecimalString(record.projection_epoch, "snapshot.projection_epoch"),
    snapshot_cursor: reqString(record.snapshot_cursor, "snapshot.snapshot_cursor"),
    next_cursor: reqNullableString(record.next_cursor, "snapshot.next_cursor"),
    events,
  };
}

// ---------------------------------------------------------------------------
// Session list (GET /sessions)
// ---------------------------------------------------------------------------

export interface ChatSessionSummary {
  session_id: string;
  title: string | null;
  [extra: string]: unknown;
}

export interface ChatSessionList {
  contract_version: string;
  sessions: ChatSessionSummary[];
}

export function parseSessionList(value: unknown): ChatSessionList {
  const record = reqRecord(value, "sessions_response");
  if (!Array.isArray(record.sessions)) {
    throw new ContractViolationError("sessions_response.sessions", "expected an array");
  }
  return {
    contract_version: reqContractVersion(
      record.contract_version,
      "sessions_response.contract_version",
    ),
    sessions: record.sessions.map((entry, index) => {
      const item = reqRecord(entry, `sessions_response.sessions[${index}]`);
      return {
        ...item,
        session_id: reqString(item.session_id, `sessions_response.sessions[${index}].session_id`),
        title:
          item.title === undefined || item.title === null
            ? null
            : reqString(item.title, `sessions_response.sessions[${index}].title`),
      };
    }),
  };
}

// ---------------------------------------------------------------------------
// Session creation (POST /sessions → 200 {session_id})
// ---------------------------------------------------------------------------

export interface SessionCreated {
  session_id: string;
  [extra: string]: unknown;
}

export function parseSessionCreated(value: unknown): SessionCreated {
  const record = reqRecord(value, "session_created");
  return {
    ...record,
    session_id: reqString(record.session_id, "session_created.session_id"),
  };
}

// ---------------------------------------------------------------------------
// Runtime config (PATCH /sessions/{id}/runtime-config)
// Responses never include api_key.
// ---------------------------------------------------------------------------

export interface RuntimeConfigUpdate {
  session_id: string;
  provider_name: string | null;
  model_name: string | null;
  base_url: string | null;
}

export function parseRuntimeConfigUpdate(value: unknown): RuntimeConfigUpdate {
  const record = reqRecord(value, "runtime_config");
  return {
    session_id: reqString(record.session_id, "runtime_config.session_id"),
    provider_name:
      record.provider_name === undefined
        ? null
        : reqNullableString(record.provider_name, "runtime_config.provider_name"),
    model_name:
      record.model_name === undefined
        ? null
        : reqNullableString(record.model_name, "runtime_config.model_name"),
    base_url:
      record.base_url === undefined
        ? null
        : reqNullableString(record.base_url, "runtime_config.base_url"),
  };
}

export interface ProviderModels {
  provider: string;
  models: string[];
  source: "live" | "unavailable";
}

export interface CodexFlowStart {
  flow_id: string;
  verification_url: string;
  user_code: string;
  expires_in: number;
}

export type CodexFlowState =
  | "pending"
  | "authorized"
  | "error"
  | "expired"
  | "cancelled"
  | string;

export interface CodexFlow {
  flow_id: string;
  state: CodexFlowState;
  verification_url?: string;
  user_code?: string;
  account_label?: string;
  error?: string;
  created_at?: string;
}

export interface OAuthAccount {
  provider: string;
  label: string;
  email?: string;
  plan?: string;
  connected_at?: string;
}

// ---------------------------------------------------------------------------
// Cancel acknowledgement (POST /sessions/{id}/cancel → 202)
// ---------------------------------------------------------------------------

export interface CancelAck {
  contract_version: string;
  session_id: string;
  run_id: string | null;
  status: "cancelling";
}

export function parseCancelAck(value: unknown): CancelAck {
  const record = reqRecord(value, "cancel_ack");
  const status = reqString(record.status, "cancel_ack.status");
  if (status !== "cancelling") {
    throw new ContractViolationError(
      "cancel_ack.status",
      `unexpected cancel status ${JSON.stringify(status)}`,
    );
  }
  return {
    contract_version: reqContractVersion(record.contract_version, "cancel_ack.contract_version"),
    session_id: reqString(record.session_id, "cancel_ack.session_id"),
    run_id: reqNullableString(record.run_id, "cancel_ack.run_id"),
    status: "cancelling",
  };
}

// ---------------------------------------------------------------------------
// Checked error bodies: {error:{code,message,retryable,replay_required?}}
// ---------------------------------------------------------------------------

export interface ApiError {
  code: string;
  message: string;
  retryable: boolean;
  replay_required?: boolean;
}

export function parseApiError(value: unknown): ApiError {
  const body = reqRecord(value, "body");
  const error = reqRecord(body.error, "body.error");
  const parsed: ApiError = {
    code: reqString(error.code, "body.error.code"),
    message: reqString(error.message, "body.error.message"),
    retryable: reqBoolean(error.retryable, "body.error.retryable"),
  };
  if ("replay_required" in error) {
    parsed.replay_required = reqBoolean(error.replay_required, "body.error.replay_required");
  }
  return parsed;
}

// ---------------------------------------------------------------------------
// Stream control frames
// ---------------------------------------------------------------------------

export type StreamControlReason =
  | "subscriber_queue_overflow"
  | "ownership_lost"
  | "sequence_loss";

export interface StreamControl {
  contract_version: string;
  kind: "replay_required";
  reason: StreamControlReason;
  /** Opaque last-safe cursor supplied by the server. */
  cursor: string;
}

const STREAM_CONTROL_REASONS: ReadonlySet<string> = new Set([
  "subscriber_queue_overflow",
  "ownership_lost",
  "sequence_loss",
]);

export function parseStreamControl(value: unknown): StreamControl {
  const record = reqRecord(value, "stream_control");
  const contractVersion = reqContractVersion(
    record.contract_version,
    "stream_control.contract_version",
  );
  const kind = reqString(record.kind, "stream_control.kind");
  if (kind !== "replay_required") {
    throw new ContractViolationError(
      "stream_control.kind",
      `unknown control kind ${JSON.stringify(kind)}`,
    );
  }
  const reason = reqString(record.reason, "stream_control.reason");
  if (!STREAM_CONTROL_REASONS.has(reason)) {
    throw new ContractViolationError(
      "stream_control.reason",
      `unknown control reason ${JSON.stringify(reason)}`,
    );
  }
  return {
    contract_version: contractVersion,
    kind: "replay_required",
    reason: reason as StreamControlReason,
    cursor: reqString(record.cursor, "stream_control.cursor"),
  };
}
