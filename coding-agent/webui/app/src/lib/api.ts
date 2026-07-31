import { parseSSE } from "./sse";
import type {
  ApprovalPolicy,
  ApprovalScope,
  CheckpointMetadata,
  DisplayStreamEvent,
  MemoryReviewRecord,
  MemoryReviewTransitionStatus,
  ProviderModels,
  RuntimeRun,
  SessionSummary,
  SessionResult,
  ThinkingConfig,
  WorkspaceDiff,
  WorkspacePatch,
} from "./types";

// PATCH /sessions/{id}/runtime-config — omitted fields stay unchanged.
// Mirrors RuntimeConfigUpdateRequest in src/coding_agent/server/schemas.py.
export interface RuntimeConfigPatch {
  model?: string;
  provider?: string;
  baseUrl?: string;
  approval?: ApprovalPolicy;
  thinking?: ThinkingConfig;
}

export interface RuntimeConfigUpdateResponse {
  session_id: string;
  provider_name: string | null;
  model_name: string | null;
  base_url: string | null;
}

export interface ClientConfig {
  baseUrl: string;
  apiKey?: string;
}

export class AgentClient {
  constructor(private cfg: ClientConfig) {}

  private url(path: string) {
    return this.cfg.baseUrl.replace(/\/+$/, "") + path;
  }
  private headers(json = false): HeadersInit {
    const h: Record<string, string> = {};
    if (this.cfg.apiKey?.trim()) h["X-API-Key"] = this.cfg.apiKey.trim();
    if (json) h["Content-Type"] = "application/json";
    return h;
  }
  private async check(r: Response): Promise<Response> {
    if (!r.ok) throw new Error(`${r.status} ${await r.text().catch(() => r.statusText)}`);
    return r;
  }

  async health(): Promise<{ status: string; sessions: number; version: string }> {
    return (await this.check(await fetch(this.url("/healthz"), { headers: this.headers() }))).json();
  }

  async createSession(opts: {
    repoPath?: string;
    approvalPolicy?: ApprovalPolicy;
    provider?: string;
    model?: string;
  }): Promise<string> {
    const body: Record<string, unknown> = {
      approval_policy: opts.approvalPolicy ?? "auto",
    };
    if (opts.repoPath?.trim()) body.repo_path = opts.repoPath.trim();
    if (opts.provider?.trim()) body.provider = opts.provider.trim();
    if (opts.model?.trim()) body.model = opts.model.trim();
    const r = await this.check(
      await fetch(this.url("/sessions"), {
        method: "POST",
        headers: this.headers(true),
        body: JSON.stringify(body),
      }),
    );
    return (await r.json()).session_id as string;
  }

  async updateRuntimeConfig(
    sessionId: string,
    opts: RuntimeConfigPatch,
  ): Promise<RuntimeConfigUpdateResponse> {
    const body: Record<string, unknown> = {};
    if (opts.model !== undefined) body.model = opts.model;
    if (opts.provider !== undefined) body.provider = opts.provider;
    if (opts.baseUrl !== undefined) body.base_url = opts.baseUrl;
    if (opts.approval !== undefined) body.approval = opts.approval;
    if (opts.thinking !== undefined) body.thinking = opts.thinking;
    return (
      await this.check(
      await fetch(this.url(`/sessions/${sessionId}/runtime-config`), {
        method: "PATCH",
        headers: this.headers(true),
        body: JSON.stringify(body),
      }),
      )
    ).json();
  }

  async listSessions(): Promise<SessionSummary[]> {
    const data = await (
      await this.check(await fetch(this.url("/sessions"), { headers: this.headers() }))
    ).json() as { sessions: SessionSummary[] };
    return data.sessions;
  }

  async closeSession(sessionId: string): Promise<{ status: string; session_id: string }> {
    return (
      await this.check(
        await fetch(this.url(`/sessions/${sessionId}`), {
          method: "DELETE",
          headers: this.headers(),
        }),
      )
    ).json();
  }

  async getSession(sessionId: string): Promise<SessionSummary> {
    return (
      await this.check(
        await fetch(this.url(`/sessions/${sessionId}`), { headers: this.headers() }),
      )
    ).json();
  }

  async displayEvents(sessionId: string): Promise<DisplayStreamEvent[]> {
    const runs = await this.runs(sessionId);
    const events: DisplayStreamEvent[] = [];
    for (const run of runs) {
      events.push(...await this.runDisplayEvents(run.run_id));
    }
    return events;
  }

  // Stream a prompt turn as user-facing DisplayEvent envelopes.
  async *prompt(
    sessionId: string,
    prompt: string,
    signal?: AbortSignal,
  ): AsyncGenerator<DisplayStreamEvent> {
    yield* this.streamDisplayPost(
      `/sessions/${sessionId}/prompt?event_format=display`,
      { prompt },
      signal,
    );
  }

  async *resume(
    sessionId: string,
    prompt?: string,
    signal?: AbortSignal,
  ): AsyncGenerator<DisplayStreamEvent> {
    const body: Record<string, unknown> = { resume_reason: "webui_resume" };
    if (prompt?.trim()) body.prompt = prompt.trim();
    yield* this.streamDisplayPost(
      `/sessions/${sessionId}/resume?event_format=display`,
      body,
      signal,
    );
  }

  async *followDisplayEvents(
    sessionId: string,
    signal?: AbortSignal,
  ): AsyncGenerator<DisplayStreamEvent> {
    const r = await this.check(
      await fetch(this.url(`/sessions/${sessionId}/display-events`), {
        headers: this.headers(),
        signal,
      }),
    );
    yield* readDisplayStream(r, signal);
  }

  async approve(
    sessionId: string,
    requestId: string,
    approved: boolean,
    feedback?: string,
    scope: ApprovalScope = "once",
  ): Promise<void> {
    await this.check(
      await fetch(this.url(`/sessions/${sessionId}/approve`), {
        method: "POST",
        headers: this.headers(true),
        body: JSON.stringify({ request_id: requestId, approved, feedback: feedback || null, scope }),
      }),
    );
  }

  async cancel(sessionId: string): Promise<void> {
    await fetch(this.url(`/sessions/${sessionId}/cancel`), {
      method: "POST",
      headers: this.headers(),
    }).catch(() => undefined);
  }

  async diff(sessionId: string): Promise<WorkspaceDiff> {
    return (
      await this.check(
        await fetch(this.url(`/sessions/${sessionId}/workspace/diff`), { headers: this.headers() }),
      )
    ).json();
  }

  async patch(sessionId: string): Promise<WorkspacePatch> {
    return (
      await this.check(
        await fetch(this.url(`/sessions/${sessionId}/workspace/patch`), { headers: this.headers() }),
      )
    ).json();
  }

  async result(sessionId: string): Promise<SessionResult> {
    return (
      await this.check(
        await fetch(this.url(`/sessions/${sessionId}/result`), { headers: this.headers() }),
      )
    ).json();
  }

  async runs(sessionId: string): Promise<RuntimeRun[]> {
    const data = await (
      await this.check(
        await fetch(this.url(`/sessions/${sessionId}/runs`), { headers: this.headers() }),
      )
    ).json() as { runs: RuntimeRun[] };
    return data.runs;
  }

  async listMemoryReviews(sessionId: string, status?: string): Promise<MemoryReviewRecord[]> {
    const params = new URLSearchParams();
    if (status?.trim()) params.set("status", status.trim());
    const suffix = params.size ? `?${params.toString()}` : "";
    const data = await (
      await this.check(
        await fetch(this.url(`/sessions/${sessionId}/memory/reviews${suffix}`), {
          headers: this.headers(),
        }),
      )
    ).json();
    return Array.isArray(data) ? (data as MemoryReviewRecord[]) : [];
  }

  // POST /sessions/{id}/memory/reviews/{candidate_id} — accept/reject/archive.
  // reason must be null or non-empty (schema: min_length 1).
  async transitionMemoryReview(
    sessionId: string,
    candidateId: string,
    status: MemoryReviewTransitionStatus,
    reason?: string,
  ): Promise<void> {
    const trimmed = reason?.trim();
    await this.check(
      await fetch(
        this.url(`/sessions/${sessionId}/memory/reviews/${encodeURIComponent(candidateId)}`),
        {
          method: "POST",
          headers: this.headers(true),
          body: JSON.stringify({ status, reason: trimmed ? trimmed : null }),
        },
      ),
    );
  }

  async listCheckpoints(sessionId: string): Promise<CheckpointMetadata[]> {
    const data = await (
      await this.check(
        await fetch(this.url(`/sessions/${sessionId}/checkpoints`), {
          headers: this.headers(),
        }),
      )
    ).json() as { checkpoints: CheckpointMetadata[] };
    return Array.isArray(data.checkpoints) ? data.checkpoints : [];
  }

  async captureCheckpoint(sessionId: string, label?: string): Promise<CheckpointMetadata> {
    const trimmed = label?.trim();
    return (
      await this.check(
        await fetch(this.url(`/sessions/${sessionId}/checkpoints`), {
          method: "POST",
          headers: this.headers(true),
          body: JSON.stringify(trimmed ? { label: trimmed } : {}),
        }),
      )
    ).json();
  }

  // Restore rewinds session history/runtime config; workspace files are unchanged.
  async restoreCheckpoint(sessionId: string, checkpointId: string): Promise<void> {
    await this.check(
      await fetch(
        this.url(
          `/sessions/${sessionId}/checkpoints/${encodeURIComponent(checkpointId)}/restore`,
        ),
        { method: "POST", headers: this.headers() },
      ),
    );
  }

  // GET /providers/{provider}/models. Unknown providers return 422 (throws);
  // provider-side failures return 200 with source="unavailable" and models=[].
  async listProviderModels(provider: string, signal?: AbortSignal): Promise<ProviderModels> {
    const r = await this.check(
      await fetch(this.url(`/providers/${encodeURIComponent(provider)}/models`), {
        headers: this.headers(),
        signal,
      }),
    );
    // Guard against malformed payloads: coerce to a safe shape instead of trusting it.
    const data: unknown = await r.json().catch(() => null);
    const raw = (data ?? {}) as { provider?: unknown; models?: unknown; source?: unknown };
    const models = Array.isArray(raw.models)
      ? raw.models.flatMap((m) => {
          const id = (m as { id?: unknown } | null)?.id;
          return typeof id === "string" && id.trim() ? [id] : [];
        })
      : [];
    return {
      provider: typeof raw.provider === "string" ? raw.provider : provider,
      models,
      source: raw.source === "live" ? "live" : "unavailable",
    };
  }

  private async runDisplayEvents(runId: string): Promise<DisplayStreamEvent[]> {
    const events: DisplayStreamEvent[] = [];
    let lastEventId: string | null = null;
    while (true) {
      const params = new URLSearchParams({ limit: "1000" });
      if (lastEventId) params.set("last_event_id", lastEventId);
      const response = await (
        await this.check(
          await fetch(this.url(`/runs/${runId}/display-events?${params.toString()}`), {
            headers: this.headers(),
          }),
        )
      ).json() as { events: Array<Record<string, unknown>> };
      const page = response.events.map(displayResponseToStreamEvent);
      events.push(...page);
      if (page.length < 1000) return events;
      const nextLastEventId = response.events[response.events.length - 1]?.source_event_id;
      if (typeof nextLastEventId !== "string") {
        throw new Error(`display event replay cursor is invalid for run ${runId}`);
      }
      if (nextLastEventId === lastEventId) {
        throw new Error(`display event replay did not advance for run ${runId}`);
      }
      lastEventId = nextLastEventId;
    }
  }

  private async *streamDisplayPost(
    path: string,
    body: Record<string, unknown>,
    signal?: AbortSignal,
  ): AsyncGenerator<DisplayStreamEvent> {
    const r = await this.check(
      await fetch(this.url(path), {
        method: "POST",
        headers: this.headers(true),
        body: JSON.stringify(body),
        signal,
      }),
    );
    yield* readDisplayStream(r, signal);
  }
}

async function* readDisplayStream(
  response: Response,
  signal?: AbortSignal,
): AsyncGenerator<DisplayStreamEvent> {
  if (!response.body) throw new Error("no response body");
  for await (const frame of parseSSE(response.body, signal)) {
    let data: unknown;
    try {
      data = JSON.parse(frame.data);
    } catch {
      data = { raw: frame.data };
    }
    yield { event: frame.event, data } as DisplayStreamEvent;
  }
}

function displayResponseToStreamEvent(record: Record<string, unknown>): DisplayStreamEvent {
  const displayKind = record.display_kind;
  if (typeof displayKind !== "string") {
    throw new Error("display event response missing display_kind");
  }
  return {
    event: displayKind,
    data: {
      source_event_id: record.source_event_id,
      run_id: record.run_id,
      sequence: record.sequence,
      display_kind: displayKind,
      payload: record.payload,
      created_at: record.created_at,
    },
  } as DisplayStreamEvent;
}
