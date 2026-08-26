import { describe, expect, it, vi } from "vitest";

import fixture from "../../../test/fixtures/connected-chat/v1/connected-chat-contract.json";

import {
  ChatApiError,
  ConnectedChatClient,
  resolveApiBase,
  type ChatStreamItem,
} from "./client";
import { ContractViolationError } from "./wire";

const SESSION = "session-01";
const BASE = "https://console.example";

function sseText(frames: Array<{ event: string; id?: string; data: unknown }>): string {
  return frames
    .map((frame) => {
      const idLine = frame.id === undefined ? "" : `id: ${frame.id}\n`;
      return `event: ${frame.event}\n${idLine}data: ${JSON.stringify(frame.data)}\n\n`;
    })
    .join("");
}

function streamResponse(text: string, chunkSize?: number): Response {
  const encoded = new TextEncoder().encode(text);
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      if (chunkSize === undefined) {
        controller.enqueue(encoded);
      } else {
        for (let i = 0; i < encoded.length; i += chunkSize) {
          controller.enqueue(encoded.subarray(i, i + chunkSize));
        }
      }
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function clientWith(fetchImpl: typeof fetch): ConnectedChatClient {
  return new ConnectedChatClient({ baseUrl: BASE, fetchImpl });
}

async function collect<T>(iter: AsyncGenerator<T>): Promise<T[]> {
  const items: T[] = [];
  for await (const item of iter) items.push(item);
  return items;
}

function chatItems(items: ChatStreamItem[]) {
  return items.filter((item) => item.type === "chat_event");
}

describe("resolveApiBase", () => {
  it("defaults to the same-origin location", () => {
    expect(resolveApiBase({}, "https://console.example")).toBe("https://console.example");
  });

  it("strips trailing slashes from the origin", () => {
    expect(resolveApiBase({}, "https://console.example/")).toBe("https://console.example");
  });

  it("honours the explicit NEXT_PUBLIC override in development", () => {
    expect(
      resolveApiBase(
        { NEXT_PUBLIC_CODING_AGENT_API_URL: "http://devbox:8080/" },
        "https://console.example",
      ),
    ).toBe("http://devbox:8080");
  });

  it("never guesses localhost when neither origin nor override exists", () => {
    expect(() => resolveApiBase({}, null)).toThrow(/NEXT_PUBLIC_CODING_AGENT_API_URL/);
    expect(() => resolveApiBase({ NEXT_PUBLIC_CODING_AGENT_API_URL: "  " }, "")).toThrow(
      /NEXT_PUBLIC_CODING_AGENT_API_URL/,
    );
  });
});

describe("ConnectedChatClient.listSessions", () => {
  it("GETs /sessions and parses the session summaries", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse(
        {
          contract_version: fixture.contract_version,
          sessions: [
            { session_id: "session-01", title: "Run tests" },
            { session_id: "session-02", title: null, future_field: true },
          ],
        },
        200,
      ),
    );
    const client = clientWith(fetchImpl as unknown as typeof fetch);

    const list = await client.listSessions();

    expect(fetchImpl).toHaveBeenCalledWith(`${BASE}/sessions`, expect.objectContaining({ method: "GET" }));
    expect(list.sessions).toHaveLength(2);
    expect(list.sessions[0]).toEqual({ session_id: "session-01", title: "Run tests", });
    expect(list.sessions[1].session_id).toBe("session-02");
    expect(list.sessions[1].title).toBeNull();
    expect(list.sessions[1].future_field).toBe(true);
  });
});

describe("ConnectedChatClient.createSession", () => {
  const request = { provider: "anthropic", model: "claude-sonnet-4" };

  it("POSTs /sessions with provider and model and parses the created session id", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse({ session_id: "session-02", extra_field: true }, 200),
    );
    const client = clientWith(fetchImpl as unknown as typeof fetch);

    const created = await client.createSession(request);

    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${BASE}/sessions`);
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual(request);
    expect(created.session_id).toBe("session-02");
    expect(created.extra_field).toBe(true);
  });

  it("includes optional base_url and api_key when provided", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({ session_id: "session-02" }, 200));
    const client = clientWith(fetchImpl as unknown as typeof fetch);

    await client.createSession({
      ...request,
      base_url: "https://api.example/v1",
      api_key: "sk-secret",
    });

    const [, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({
      provider: "anthropic",
      model: "claude-sonnet-4",
      base_url: "https://api.example/v1",
      api_key: "sk-secret",
    });
  });

  it("omits empty optional fields from the create body", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({ session_id: "session-02" }, 200));
    const client = clientWith(fetchImpl as unknown as typeof fetch);

    await client.createSession({ ...request, base_url: "  ", api_key: "" });

    const [, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual(request);
  });

  it("rejects a create response without a session id", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({ id: "session-02" }, 200));
    const client = clientWith(fetchImpl as unknown as typeof fetch);

    await expect(client.createSession(request)).rejects.toBeInstanceOf(ContractViolationError);
  });

  it("surfaces a checked error body from a failed create", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse(
        { error: { code: "provider_unavailable", message: "No provider configured", retryable: false } },
        500,
      ),
    );
    const client = clientWith(fetchImpl as unknown as typeof fetch);

    const failure = await client.createSession(request).catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ChatApiError);
    expect((failure as ChatApiError).status).toBe(500);
    expect((failure as ChatApiError).error.code).toBe("provider_unavailable");
  });
});

describe("ConnectedChatClient.updateRuntimeConfig", () => {
  it("PATCHes /sessions/{id}/runtime-config and never treats api_key as a response field", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse(
        {
          session_id: SESSION,
          provider_name: "deepseek",
          model_name: "deepseek-chat",
          base_url: "https://api.deepseek.com",
          api_key: "should-not-leak",
        },
        200,
      ),
    );
    const client = clientWith(fetchImpl as unknown as typeof fetch);

    const updated = await client.updateRuntimeConfig(SESSION, {
      provider: "deepseek",
      model: "deepseek-chat",
      base_url: "https://api.deepseek.com",
      api_key: "sk-secret",
    });

    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${BASE}/sessions/${SESSION}/runtime-config`);
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(String(init.body))).toEqual({
      provider: "deepseek",
      model: "deepseek-chat",
      base_url: "https://api.deepseek.com",
      api_key: "sk-secret",
    });
    expect(updated.session_id).toBe(SESSION);
    expect(updated.provider_name).toBe("deepseek");
    expect(updated.model_name).toBe("deepseek-chat");
    expect(updated.base_url).toBe("https://api.deepseek.com");
    expect(updated).not.toHaveProperty("api_key");
  });

  it("surfaces FastAPI detail text from a failed PATCH instead of a contract violation", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse({ detail: "session tape target cannot be rebound" }, 500),
    );
    const client = clientWith(fetchImpl as unknown as typeof fetch);

    const failure = await client
      .updateRuntimeConfig(SESSION, { provider: "deepseek", model: "deepseek-chat" })
      .catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(ChatApiError);
    expect(failure).not.toBeInstanceOf(ContractViolationError);
    expect((failure as ChatApiError).status).toBe(500);
    expect((failure as ChatApiError).error.message).toBe("session tape target cannot be rebound");
  });
});

describe("ConnectedChatClient.listProviderModels", () => {
  it("GETs /providers/{p}/models and returns live ids", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse(
        {
          provider: "anthropic",
          source: "live",
          models: [{ id: "claude-sonnet-4" }, { id: "claude-opus-4" }],
        },
        200,
      ),
    );
    const client = clientWith(fetchImpl as unknown as typeof fetch);

    const listed = await client.listProviderModels("anthropic");
    expect(fetchImpl).toHaveBeenCalledWith(
      `${BASE}/providers/anthropic/models`,
      expect.objectContaining({ method: "GET" }),
    );
    expect(listed).toEqual({
      provider: "anthropic",
      source: "live",
      models: ["claude-sonnet-4", "claude-opus-4"],
    });
  });

  it("fails when a successful body is not JSON", async () => {
    const fetchImpl = vi.fn(async () => new Response("<html>nope</html>", {
      status: 200,
      headers: { "content-type": "text/html" },
    }));
    const client = clientWith(fetchImpl as unknown as typeof fetch);
    await expect(client.listProviderModels("anthropic")).rejects.toBeInstanceOf(
      ContractViolationError,
    );
  });

  it("rejects an empty JSON object as a contract violation", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({}, 200));
    const client = clientWith(fetchImpl as unknown as typeof fetch);
    await expect(client.listProviderModels("anthropic")).rejects.toBeInstanceOf(
      ContractViolationError,
    );
  });
});

describe("ConnectedChatClient Codex OAuth", () => {
  it("POSTs /oauth/codex/start with an optional label", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse(
        {
          flow_id: "flow-1",
          verification_url: "https://auth.example/device",
          user_code: "ABCD-EFGH",
          expires_in: 900,
        },
        200,
      ),
    );
    const client = clientWith(fetchImpl as unknown as typeof fetch);

    const started = await client.startCodexOAuth("work");
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${BASE}/oauth/codex/start`);
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ label: "work" });
    expect(started.flow_id).toBe("flow-1");
    expect(started.user_code).toBe("ABCD-EFGH");
  });

  it("throws ChatApiError 404 when start or list hits a missing endpoint", async () => {
    const fetchImpl = vi.fn(async () => new Response("not found", { status: 404 }));
    const client = clientWith(fetchImpl as unknown as typeof fetch);

    const startFailure = await client.startCodexOAuth().catch((error: unknown) => error);
    expect(startFailure).toBeInstanceOf(ChatApiError);
    expect((startFailure as ChatApiError).status).toBe(404);

    const listFailure = await client.listCodexFlows().catch((error: unknown) => error);
    expect(listFailure).toBeInstanceOf(ChatApiError);
    expect((listFailure as ChatApiError).status).toBe(404);
  });
});

describe("ConnectedChatClient.snapshot", () => {
  it("GETs the fixture snapshot path and parses the envelope", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse(fixture.http.snapshot.response, 200));
    const client = clientWith(fetchImpl as unknown as typeof fetch);

    const snapshot = await client.snapshot(SESSION, { limit: 2 });

    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${BASE}/sessions/session-01/chat-events?limit=2`);
    expect(init.method).toBe("GET");
    expect(snapshot.session_id).toBe("session-01");
    expect(snapshot.snapshot_cursor).toBe(fixture.http.snapshot.response.snapshot_cursor);
    expect(snapshot.next_cursor).toBe(fixture.http.snapshot.response.next_cursor);
  });

  it("passes the cursor as an opaque query parameter", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse(fixture.http.snapshot.response, 200));
    const client = clientWith(fetchImpl as unknown as typeof fetch);

    await client.snapshot(SESSION, { cursor: fixture.http.follow.cursor, limit: 2 });

    const [url] = fetchImpl.mock.calls[0] as unknown as [string];
    expect(url).toContain(`cursor=${encodeURIComponent(fixture.http.follow.cursor)}`);
  });
});

describe("ConnectedChatClient.follow SSE framing", () => {
  function followClient(text: string, chunkSize?: number) {
    const fetchImpl = vi.fn(async () => streamResponse(text, chunkSize));
    return {
      client: clientWith(fetchImpl as unknown as typeof fetch),
      fetchImpl,
    };
  }

  it("GETs the fixture follow path and yields parsed chat envelopes with ids", async () => {
    const { client, fetchImpl } = followClient(sseText(fixture.events.slice(0, 2)));
    const items = await collect(client.follow(SESSION, fixture.http.follow.cursor));

    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(
      `${BASE}/sessions/session-01/chat-events/follow?cursor=${encodeURIComponent(fixture.http.follow.cursor)}`,
    );
    expect(init.method).toBe("GET");
    expect(new Headers(init.headers).get("accept")).toBe("text/event-stream");

    expect(items).toHaveLength(2);
    const [first, second] = chatItems(items);
    expect(first.type).toBe("chat_event");
    if (first.type !== "chat_event") throw new Error("unreachable");
    expect(first.id).toBe("12");
    expect(first.event.source_event_id).toBe("evt-user-01");
    expect(first.event.kind).toBe("user_prompt");
    if (second.type !== "chat_event") throw new Error("unreachable");
    expect(second.event.kind).toBe("thinking");
  });

  it("yields stream-control frames with exact reasons", async () => {
    const { client } = followClient(sseText(fixture.stream_controls));
    const items = await collect(client.follow(SESSION, fixture.http.follow.cursor));

    expect(items).toHaveLength(3);
    expect(items.map((item) => (item.type === "stream_control" ? item.control.reason : ""))).toEqual([
      "subscriber_queue_overflow",
      "ownership_lost",
      "sequence_loss",
    ]);
  });

  it("handles CRLF line endings", async () => {
    const lf = sseText(fixture.events.slice(0, 1));
    const { client } = followClient(lf.replaceAll("\n", "\r\n"));
    const items = await collect(client.follow(SESSION, fixture.http.follow.cursor));
    expect(chatItems(items)).toHaveLength(1);
  });

  it("handles frames split across arbitrarily small chunks", async () => {
    const { client } = followClient(sseText(fixture.events.slice(0, 3)), 7);
    const items = await collect(client.follow(SESSION, fixture.http.follow.cursor));
    expect(chatItems(items).map((item) => item.event.session_seq)).toEqual(["12", "13", "14"]);
  });

  it("joins multiline data fields with newlines", async () => {
    const raw =
      "event: chat_event\n" +
      'data: {"contract_version":"1.0.0",\n' +
      'data: "source_event_id":"evt-x","session_seq":"30","session_id":"session-01","run_id":null,\n' +
      'data: "kind":"thinking","created_at":"2026-08-24T00:00:01Z","payload":{"text":"hi"}}\n\n';
    const { client } = followClient(raw);
    const items = await collect(client.follow(SESSION, fixture.http.follow.cursor));
    expect(chatItems(items)).toHaveLength(1);
    expect(chatItems(items)[0].event.source_event_id).toBe("evt-x");
  });

  it("ignores comment heartbeat lines", async () => {
    const raw = `: hb\n\n: hb2\n${sseText(fixture.events.slice(0, 1))}: tail-hb\n`;
    const { client } = followClient(raw);
    const items = await collect(client.follow(SESSION, fixture.http.follow.cursor));
    expect(chatItems(items)).toHaveLength(1);
  });

  it("flushes a trailing frame that lacks the final blank line", async () => {
    const raw = sseText(fixture.events.slice(0, 1)).replace(/\n\n$/, "\n");
    const { client } = followClient(raw);
    const items = await collect(client.follow(SESSION, fixture.http.follow.cursor));
    expect(chatItems(items)).toHaveLength(1);
  });

  it("treats EOF as non-terminal: the generator just completes", async () => {
    const { client } = followClient(sseText(fixture.events.slice(0, 2)));
    const items = await collect(client.follow(SESSION, fixture.http.follow.cursor));
    expect(items).toHaveLength(2);
  });

  it("ends iteration when the abort signal fires", async () => {
    const frame = new TextEncoder().encode(sseText(fixture.events.slice(0, 1)));
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(frame); // never closes
      },
    });
    const fetchImpl = vi.fn(async () =>
      Promise.resolve(
        new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } }),
      ),
    );
    const client = clientWith(fetchImpl as unknown as typeof fetch);
    const abort = new AbortController();

    const iter = client.follow(SESSION, fixture.http.follow.cursor, abort.signal);
    const first = await iter.next();
    expect(first.done).toBe(false);
    abort.abort();
    const after = await iter.next();
    expect(after.done).toBe(true);
  });

  it("surfaces fixture cursor errors as checked ChatApiError values", async () => {
    const expired = fixture.cursor.errors.find((e) => e.case === "expired");
    if (!expired) throw new Error("fixture missing expired case");
    const fetchImpl = vi.fn(async () =>
      jsonResponse(
        {
          error: {
            code: expired.reason,
            message: "cursor expired",
            retryable: false,
            replay_required: expired.replay_required,
          },
        },
        expired.status,
      ),
    );
    const client = clientWith(fetchImpl as unknown as typeof fetch);

    const failure = await collect(client.follow(SESSION, fixture.http.follow.cursor)).catch(
      (error: unknown) => error,
    );
    expect(failure).toBeInstanceOf(ChatApiError);
    const apiError = failure as ChatApiError;
    expect(apiError.status).toBe(410);
    expect(apiError.error.code).toBe("cursor_expired");
    expect(apiError.error.replay_required).toBe(true);
  });

  it("rejects non-JSON error bodies instead of fabricating one", async () => {
    const fetchImpl = vi.fn(async () =>
      Promise.resolve(new Response("<html>bad gateway</html>", { status: 502 })),
    );
    const client = clientWith(fetchImpl as unknown as typeof fetch);
    await expect(collect(client.follow(SESSION, fixture.http.follow.cursor))).rejects.toThrow(
      ContractViolationError,
    );
  });
});

describe("ConnectedChatClient.prompt", () => {
  it("POSTs the fixture prompt request and streams the ordered turn events", async () => {
    const fetchImpl = vi.fn(async () => streamResponse(sseText(fixture.events.slice(0, 7))));
    const client = clientWith(fetchImpl as unknown as typeof fetch);

    const items = await collect(client.prompt(SESSION, fixture.http.prompt.request));

    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${BASE}/sessions/session-01/prompt`);
    expect(init.method).toBe("POST");
    expect(new Headers(init.headers).get("content-type")).toBe("application/json");
    expect(JSON.parse(String(init.body))).toEqual({ prompt: "Run tests", command_id: "cmd-01" });

    const kinds = chatItems(items).map((item) => item.event.kind);
    expect(kinds).toEqual([
      "user_prompt",
      "thinking",
      "progress",
      "tool_call",
      "tool_result",
      "assistant_message",
      "root_terminal",
    ]);
  });

  it("maps named admission errors with status and retryable flag", async () => {
    for (const entry of fixture.http.errors.admission) {
      const fetchImpl = vi.fn(async () => jsonResponse(entry.body, entry.status));
      const client = clientWith(fetchImpl as unknown as typeof fetch);
      const failure = await collect(
        client.prompt(SESSION, fixture.http.prompt.request),
      ).catch((error: unknown) => error);
      expect(failure).toBeInstanceOf(ChatApiError);
      const apiError = failure as ChatApiError;
      expect(apiError.status).toBe(entry.status);
      expect(apiError.error.code).toBe(entry.body.error.code);
      expect(apiError.error.retryable).toBe(entry.body.error.retryable);
    }
  });
});

describe("ConnectedChatClient.resume", () => {
  it("POSTs the fixture resume request and streams the new run", async () => {
    const fetchImpl = vi.fn(async () => streamResponse(sseText(fixture.events.slice(0, 1))));
    const client = clientWith(fetchImpl as unknown as typeof fetch);

    const items = await collect(client.resume(SESSION, fixture.http.resume.request));

    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${BASE}/sessions/session-01/resume`);
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({
      command_id: "cmd-02",
      parent_run_id: "run-01",
      prompt: null,
    });
    expect(chatItems(items)).toHaveLength(1);
  });

  it("surfaces resume_source_unsettled as a checked 409", async () => {
    const entry = fixture.http.errors.lifecycle.find((e) => e.body.error.code === "resume_source_unsettled");
    if (!entry) throw new Error("fixture missing lifecycle case");
    const fetchImpl = vi.fn(async () => jsonResponse(entry.body, entry.status));
    const client = clientWith(fetchImpl as unknown as typeof fetch);
    const failure = await collect(
      client.resume(SESSION, fixture.http.resume.request),
    ).catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ChatApiError);
    expect((failure as ChatApiError).error.code).toBe("resume_source_unsettled");
  });
});

describe("ConnectedChatClient.cancel", () => {
  it("POSTs cancel and parses the 202 acknowledgement", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse(fixture.http.cancel.response, 202));
    const client = clientWith(fetchImpl as unknown as typeof fetch);

    const ack = await client.cancel(SESSION);

    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${BASE}/sessions/session-01/cancel`);
    expect(init.method).toBe("POST");
    expect(ack.session_id).toBe("session-01");
    expect(ack.run_id).toBe("run-01");
    expect(ack.status).toBe("cancelling");
  });

  it("surfaces no_active_turn as a checked 409", async () => {
    const entry = fixture.http.errors.lifecycle.find((e) => e.body.error.code === "no_active_turn");
    if (!entry) throw new Error("fixture missing no_active_turn");
    const fetchImpl = vi.fn(async () => jsonResponse(entry.body, entry.status));
    const client = clientWith(fetchImpl as unknown as typeof fetch);
    const failure = await client.cancel(SESSION).catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ChatApiError);
    expect((failure as ChatApiError).status).toBe(409);
    expect((failure as ChatApiError).error.code).toBe("no_active_turn");
  });
});

describe("ConnectedChatClient auth taxonomy", () => {
  const entry = fixture.http.errors.auth[0];
  const expectedError = {
    code: "credentials_required",
    message: "Authentication credentials are required",
    retryable: false,
  };

  it.each([
    { name: "listSessions", run: (client: ConnectedChatClient) => client.listSessions() },
    { name: "createSession", run: (client: ConnectedChatClient) => client.createSession({ provider: "anthropic", model: "claude-sonnet-4" }) },
    { name: "snapshot", run: (client: ConnectedChatClient) => client.snapshot(SESSION, { limit: 2 }) },
    {
      name: "follow",
      run: (client: ConnectedChatClient) => collect(client.follow(SESSION, fixture.http.follow.cursor)),
    },
    {
      name: "prompt",
      run: (client: ConnectedChatClient) => collect(client.prompt(SESSION, fixture.http.prompt.request)),
    },
    {
      name: "resume",
      run: (client: ConnectedChatClient) => collect(client.resume(SESSION, fixture.http.resume.request)),
    },
    { name: "cancel", run: (client: ConnectedChatClient) => client.cancel(SESSION) },
  ])(
    "surfaces enabled-auth 401 from $name as credentials_required, never ContractViolationError",
    async ({ run }) => {
      const fetchImpl = vi.fn(async () => jsonResponse(entry.body, entry.status));
      const client = clientWith(fetchImpl as unknown as typeof fetch);

      const failure = await run(client).catch((error: unknown) => error);
      expect(failure).toBeInstanceOf(ChatApiError);
      expect(failure).not.toBeInstanceOf(ContractViolationError);
      const apiError = failure as ChatApiError;
      expect(apiError.status).toBe(401);
      expect(apiError.error).toEqual(expectedError);
    },
  );
});
