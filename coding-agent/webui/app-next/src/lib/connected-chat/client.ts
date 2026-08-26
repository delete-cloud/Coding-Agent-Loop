// REST and fetch-SSE client for the connected-chat v1 contract.
//
// All transport lives here: base-URL resolution, checked JSON errors, and
// fetch/ReadableStream SSE for both GET (follow) and POST (prompt/resume).
// Every payload crosses the wire boundary parsers in ./wire — this module
// never fabricates or defaults contract data. Cursors stay opaque strings.

import { parseSseStream } from "./sse";
import {
  ContractViolationError,
  parseApiError,
  parseCancelAck,
  parseChatEvent,
  parseChatSnapshot,
  parseRuntimeConfigUpdate,
  parseSessionCreated,
  parseSessionList,
  parseStreamControl,
  type ApiError,
  type CancelAck,
  type ChatEventEnvelope,
  type ChatSessionList,
  type ChatSnapshot,
  type CodexFlow,
  type CodexFlowStart,
  type OAuthAccount,
  type ProviderModels,
  type RuntimeConfigUpdate,
  type SessionCreated,
  type StreamControl,
} from "./wire";

/** A checked non-2xx response whose body carried a contract error envelope. */
export class ChatApiError extends Error {
  readonly status: number;
  readonly error: ApiError;

  constructor(status: number, error: ApiError) {
    super(`HTTP ${status}: ${error.code} — ${error.message}`);
    this.name = "ChatApiError";
    this.status = status;
    this.error = error;
  }
}

/** Items yielded by both owning (POST) and passive (GET) SSE streams. */
export type ChatStreamItem =
  | { type: "chat_event"; id: string | null; event: ChatEventEnvelope }
  | { type: "stream_control"; control: StreamControl };

export interface ConnectedChatClientOptions {
  baseUrl: string;
  fetchImpl?: typeof fetch;
}

export interface PromptRequest {
  prompt: string;
  command_id: string;
}

export interface ResumeRequest {
  command_id: string;
  parent_run_id: string;
  prompt: string | null;
}

export interface CreateSessionRequest {
  provider: string;
  model: string;
  base_url?: string;
  api_key?: string;
}

export interface RuntimeConfigPatch {
  provider?: string;
  model?: string;
  base_url?: string;
  api_key?: string;
}

export function isNotFoundError(error: unknown): boolean {
  if (error instanceof ChatApiError) return error.status === 404;
  if (typeof error === "object" && error !== null && "status" in error) {
    return error.status === 404;
  }
  return error instanceof Error && /HTTP 404\b/.test(error.message);
}

function sessionConfigBody(request: RuntimeConfigPatch): Record<string, string> {
  const body: Record<string, string> = {};
  if (request.provider?.trim()) body.provider = request.provider.trim();
  if (request.model?.trim()) body.model = request.model.trim();
  if (request.base_url?.trim()) body.base_url = request.base_url.trim();
  if (request.api_key?.trim()) body.api_key = request.api_key.trim();
  return body;
}

/**
 * Resolve the API base URL. Production is same-origin: the page's own origin
 * is the default. The only override is the explicit NEXT_PUBLIC env var for
 * development. There is deliberately no implicit localhost fallback.
 */
export function resolveApiBase(
  env: Readonly<Record<string, string | undefined>>,
  locationOrigin: string | null | undefined,
): string {
  const override = env.NEXT_PUBLIC_CODING_AGENT_API_URL?.trim();
  if (override) return override.replace(/\/+$/, "");
  const origin = locationOrigin?.trim();
  if (origin) return origin.replace(/\/+$/, "");
  throw new Error(
    "API base unresolved: no same-origin location available and " +
      "NEXT_PUBLIC_CODING_AGENT_API_URL is not set",
  );
}

export class ConnectedChatClient {
  private readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;

  constructor(options: ConnectedChatClientOptions) {
    if (!options.baseUrl) {
      throw new Error("ConnectedChatClient requires a non-empty baseUrl");
    }
    this.baseUrl = options.baseUrl.replace(/\/+$/, "");
    this.fetchImpl = options.fetchImpl ?? fetch.bind(globalThis);
  }

  private sessionPath(sessionId: string, suffix: string): string {
    return `${this.baseUrl}/sessions/${encodeURIComponent(sessionId)}${suffix}`;
  }

  private async parseErrorResponse(response: Response): Promise<never> {
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      throw new ContractViolationError(
        "error_body",
        `HTTP ${response.status} error body is not valid JSON`,
      );
    }
    try {
      throw new ChatApiError(response.status, parseApiError(body));
    } catch (cause) {
      if (cause instanceof ChatApiError) throw cause;
      if (!(cause instanceof ContractViolationError)) throw cause;
      const detail = fastapiDetail(body);
      if (detail !== null) {
        throw new ChatApiError(response.status, {
          code: "http_error",
          message: detail,
          retryable: false,
        });
      }
      throw new ContractViolationError(
        cause.path,
        `HTTP ${response.status} error body violates the contract: ${cause.message}`,
      );
    }
  }

  private async getJson<T>(url: string, parse: (body: unknown) => T, signal?: AbortSignal): Promise<T> {
    const response = await this.fetchImpl(url, {
      method: "GET",
      headers: { accept: "application/json" },
      signal: signal ?? null,
    });
    if (!response.ok) await this.parseErrorResponse(response);
    return parse(await response.json());
  }

  private async *stream(
    response: Response,
    signal?: AbortSignal,
  ): AsyncGenerator<ChatStreamItem> {
    if (!response.ok) await this.parseErrorResponse(response);
    if (response.body === null) {
      throw new ContractViolationError("stream", `HTTP ${response.status} response has no body`);
    }
    for await (const frame of parseSseStream(response.body, signal)) {
      if (signal?.aborted) return;
      let data: unknown;
      try {
        data = JSON.parse(frame.data);
      } catch {
        throw new ContractViolationError(
          `stream.${frame.event}`,
          "SSE data field is not valid JSON",
        );
      }
      if (frame.event === "chat_event") {
        yield { type: "chat_event", id: frame.id, event: parseChatEvent(data) };
      } else if (frame.event === "stream_control") {
        yield { type: "stream_control", control: parseStreamControl(data) };
      } else {
        throw new ContractViolationError(
          "stream.event",
          `unknown SSE event name ${JSON.stringify(frame.event)}`,
        );
      }
    }
  }

  listSessions(signal?: AbortSignal): Promise<ChatSessionList> {
    return this.getJson(`${this.baseUrl}/sessions`, parseSessionList, signal);
  }

  /**
   * Create a session with the closed provider list. Optional base_url / api_key
   * are session-scoped process memory on the server and are never echoed back.
   */
  async createSession(
    request: CreateSessionRequest,
    signal?: AbortSignal,
  ): Promise<SessionCreated> {
    const response = await this.fetchImpl(`${this.baseUrl}/sessions`, {
      method: "POST",
      headers: { accept: "application/json", "content-type": "application/json" },
      body: JSON.stringify(sessionConfigBody(request)),
      signal: signal ?? null,
    });
    if (!response.ok) await this.parseErrorResponse(response);
    return parseSessionCreated(await response.json());
  }

  async updateRuntimeConfig(
    sessionId: string,
    patch: RuntimeConfigPatch,
    signal?: AbortSignal,
  ): Promise<RuntimeConfigUpdate> {
    const response = await this.fetchImpl(this.sessionPath(sessionId, "/runtime-config"), {
      method: "PATCH",
      headers: { accept: "application/json", "content-type": "application/json" },
      body: JSON.stringify(sessionConfigBody(patch)),
      signal: signal ?? null,
    });
    if (!response.ok) await this.parseErrorResponse(response);
    return parseRuntimeConfigUpdate(await response.json());
  }

  async listProviderModels(provider: string, signal?: AbortSignal): Promise<ProviderModels> {
    const response = await this.fetchImpl(
      `${this.baseUrl}/providers/${encodeURIComponent(provider)}/models`,
      {
        method: "GET",
        headers: { accept: "application/json" },
        signal: signal ?? null,
      },
    );
    if (!response.ok) await this.throwLooseHttpError(response);
    const data: unknown = await response.json().catch(() => null);
    return parseProviderModels(data, provider);
  }

  async startCodexOAuth(label?: string, signal?: AbortSignal): Promise<CodexFlowStart> {
    const body: Record<string, string> = {};
    if (label?.trim()) body.label = label.trim();
    const response = await this.fetchImpl(`${this.baseUrl}/oauth/codex/start`, {
      method: "POST",
      headers: { accept: "application/json", "content-type": "application/json" },
      body: JSON.stringify(body),
      signal: signal ?? null,
    });
    if (!response.ok) await this.throwLooseHttpError(response);
    return parseCodexFlowStart(await response.json().catch(() => null));
  }

  async listCodexFlows(signal?: AbortSignal): Promise<CodexFlow[]> {
    const response = await this.fetchImpl(`${this.baseUrl}/oauth/codex/flows`, {
      method: "GET",
      headers: { accept: "application/json" },
      signal: signal ?? null,
    });
    if (!response.ok) await this.throwLooseHttpError(response);
    return parseCodexFlowList(await response.json().catch(() => null));
  }

  async getCodexFlow(flowId: string, signal?: AbortSignal): Promise<CodexFlow> {
    const response = await this.fetchImpl(
      `${this.baseUrl}/oauth/codex/flows/${encodeURIComponent(flowId)}`,
      {
        method: "GET",
        headers: { accept: "application/json" },
        signal: signal ?? null,
      },
    );
    if (!response.ok) await this.throwLooseHttpError(response);
    const parsed = parseCodexFlowList([await response.json().catch(() => null)]);
    if (parsed.length === 0) {
      throw new ContractViolationError("codex_flow.flow_id", "codex oauth flow response missing flow_id");
    }
    return parsed[0];
  }

  async cancelCodexFlow(flowId: string, signal?: AbortSignal): Promise<void> {
    const response = await this.fetchImpl(
      `${this.baseUrl}/oauth/codex/flows/${encodeURIComponent(flowId)}/cancel`,
      {
        method: "POST",
        headers: { accept: "application/json" },
        signal: signal ?? null,
      },
    );
    if (!response.ok) await this.throwLooseHttpError(response);
  }

  async listOAuthAccounts(signal?: AbortSignal): Promise<OAuthAccount[]> {
    const response = await this.fetchImpl(`${this.baseUrl}/oauth/accounts`, {
      method: "GET",
      headers: { accept: "application/json" },
      signal: signal ?? null,
    });
    if (!response.ok) await this.throwLooseHttpError(response);
    return parseOAuthAccounts(await response.json().catch(() => null));
  }

  async deleteOAuthAccount(providerKey: string, signal?: AbortSignal): Promise<void> {
    const response = await this.fetchImpl(
      `${this.baseUrl}/oauth/accounts/${encodeURIComponent(providerKey)}`,
      {
        method: "DELETE",
        headers: { accept: "application/json" },
        signal: signal ?? null,
      },
    );
    if (!response.ok) await this.throwLooseHttpError(response);
  }

  private async throwLooseHttpError(response: Response): Promise<never> {
    if (response.status === 404) {
      const message = (await response.text().catch(() => "not found")) || "not found";
      throw new ChatApiError(404, { code: "not_found", message, retryable: false });
    }
    await this.parseErrorResponse(response);
    throw new Error(`HTTP ${response.status}`);
  }

  snapshot(
    sessionId: string,
    options: { cursor?: string; limit?: number } = {},
    signal?: AbortSignal,
  ): Promise<ChatSnapshot> {
    const params = new URLSearchParams();
    if (options.cursor !== undefined) params.set("cursor", options.cursor);
    if (options.limit !== undefined) params.set("limit", String(options.limit));
    const query = params.toString();
    const url = `${this.sessionPath(sessionId, "/chat-events")}${query ? `?${query}` : ""}`;
    return this.getJson(url, parseChatSnapshot, signal);
  }

  async *follow(
    sessionId: string,
    cursor: string,
    signal?: AbortSignal,
  ): AsyncGenerator<ChatStreamItem> {
    const params = new URLSearchParams({ cursor });
    const response = await this.fetchImpl(
      `${this.sessionPath(sessionId, `/chat-events/follow`)}?${params}`,
      {
        method: "GET",
        headers: { accept: "text/event-stream" },
        signal: signal ?? null,
      },
    );
    yield* this.stream(response, signal);
  }

  async *prompt(
    sessionId: string,
    request: PromptRequest,
    signal?: AbortSignal,
  ): AsyncGenerator<ChatStreamItem> {
    yield* this.postStream(this.sessionPath(sessionId, "/prompt"), request, signal);
  }

  async *resume(
    sessionId: string,
    request: ResumeRequest,
    signal?: AbortSignal,
  ): AsyncGenerator<ChatStreamItem> {
    yield* this.postStream(this.sessionPath(sessionId, "/resume"), request, signal);
  }

  private async *postStream(
    url: string,
    body: unknown,
    signal?: AbortSignal,
  ): AsyncGenerator<ChatStreamItem> {
    const response = await this.fetchImpl(url, {
      method: "POST",
      headers: { accept: "text/event-stream", "content-type": "application/json" },
      body: JSON.stringify(body),
      signal: signal ?? null,
    });
    yield* this.stream(response, signal);
  }

  async cancel(sessionId: string, signal?: AbortSignal): Promise<CancelAck> {
    const response = await this.fetchImpl(this.sessionPath(sessionId, "/cancel"), {
      method: "POST",
      headers: { accept: "application/json" },
      signal: signal ?? null,
    });
    if (!response.ok) await this.parseErrorResponse(response);
    return parseCancelAck(await response.json());
  }
}

function fastapiDetail(body: unknown): string | null {
  if (typeof body !== "object" || body === null || !("detail" in body)) return null;
  const detail = body.detail;
  if (typeof detail === "string" && detail.trim()) return detail.trim();
  return null;
}

function asRecord(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return {};
  const record: Record<string, unknown> = {};
  for (const [key, entry] of Object.entries(value)) {
    record[key] = entry;
  }
  return record;
}

function parseProviderModels(value: unknown, fallbackProvider: string): ProviderModels {
  const raw = asRecord(value);
  const models: string[] = [];
  if (Array.isArray(raw.models)) {
    for (const entry of raw.models) {
      if (typeof entry === "string" && entry.trim()) {
        models.push(entry.trim());
        continue;
      }
      const item = asRecord(entry);
      if (typeof item.id === "string" && item.id.trim()) models.push(item.id.trim());
    }
  }
  return {
    provider: typeof raw.provider === "string" && raw.provider.trim() ? raw.provider : fallbackProvider,
    models,
    source: raw.source === "live" ? "live" : "unavailable",
  };
}

function parseCodexFlowStart(value: unknown): CodexFlowStart {
  const raw = asRecord(value);
  if (typeof raw.flow_id !== "string" || !raw.flow_id) {
    throw new ContractViolationError("codex_start.flow_id", "codex oauth start response missing flow_id");
  }
  return {
    flow_id: raw.flow_id,
    verification_url: typeof raw.verification_url === "string" ? raw.verification_url : "",
    user_code: typeof raw.user_code === "string" ? raw.user_code : "",
    expires_in: typeof raw.expires_in === "number" ? raw.expires_in : 0,
  };
}

function parseCodexFlowItem(value: unknown): CodexFlow | null {
  const raw = asRecord(value);
  if (typeof raw.flow_id !== "string" || !raw.flow_id) return null;
  const flow: CodexFlow = {
    flow_id: raw.flow_id,
    state: typeof raw.state === "string" && raw.state ? raw.state : "pending",
  };
  if (typeof raw.verification_url === "string") flow.verification_url = raw.verification_url;
  if (typeof raw.user_code === "string") flow.user_code = raw.user_code;
  if (typeof raw.account_label === "string") flow.account_label = raw.account_label;
  if (typeof raw.error === "string") flow.error = raw.error;
  if (typeof raw.created_at === "string") flow.created_at = raw.created_at;
  return flow;
}

function parseCodexFlowList(value: unknown): CodexFlow[] {
  const items = Array.isArray(value)
    ? value
    : Array.isArray(asRecord(value).flows)
      ? asRecord(value).flows
      : [];
  if (!Array.isArray(items)) return [];
  const flows: CodexFlow[] = [];
  for (const item of items) {
    const parsed = parseCodexFlowItem(item);
    if (parsed) flows.push(parsed);
  }
  return flows;
}

function parseOAuthAccounts(value: unknown): OAuthAccount[] {
  const items = Array.isArray(value)
    ? value
    : Array.isArray(asRecord(value).accounts)
      ? asRecord(value).accounts
      : [];
  if (!Array.isArray(items)) return [];
  const accounts: OAuthAccount[] = [];
  for (const item of items) {
    const raw = asRecord(item);
    if (typeof raw.provider !== "string" || !raw.provider) continue;
    const account: OAuthAccount = {
      provider: raw.provider,
      label: typeof raw.label === "string" ? raw.label : raw.provider,
    };
    if (typeof raw.email === "string") account.email = raw.email;
    if (typeof raw.plan === "string") account.plan = raw.plan;
    if (typeof raw.connected_at === "string") account.connected_at = raw.connected_at;
    accounts.push(account);
  }
  return accounts;
}
