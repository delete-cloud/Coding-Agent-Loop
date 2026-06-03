import { parseSSE } from "./sse";
import type {
  ApprovalPolicy,
  ApprovalScope,
  DisplayStreamEvent,
  SessionResult,
  WorkspaceDiff,
} from "./types";

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
    model?: string;
  }): Promise<string> {
    const body: Record<string, unknown> = {
      approval_policy: opts.approvalPolicy ?? "auto",
    };
    if (opts.repoPath?.trim()) body.repo_path = opts.repoPath.trim();
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

  // Stream a prompt turn as user-facing DisplayEvent envelopes.
  async *prompt(
    sessionId: string,
    prompt: string,
    signal?: AbortSignal,
  ): AsyncGenerator<DisplayStreamEvent> {
    const r = await this.check(
      await fetch(
        this.url(`/sessions/${sessionId}/prompt?event_format=display`),
        {
          method: "POST",
          headers: this.headers(true),
          body: JSON.stringify({ prompt }),
          signal,
        },
      ),
    );
    if (!r.body) throw new Error("no response body");
    for await (const frame of parseSSE(r.body, signal)) {
      let data: unknown;
      try {
        data = JSON.parse(frame.data);
      } catch {
        data = { raw: frame.data };
      }
      yield { event: frame.event, data } as DisplayStreamEvent;
    }
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

  async result(sessionId: string): Promise<SessionResult> {
    return (
      await this.check(
        await fetch(this.url(`/sessions/${sessionId}/result`), { headers: this.headers() }),
      )
    ).json();
  }
}
