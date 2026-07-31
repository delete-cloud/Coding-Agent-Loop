// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AgentClient } from "../lib/api";
import CodexAccountsCard from "./CodexAccountsCard";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const client = new AgentClient({ baseUrl: "http://test" });

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const account = (provider: string, overrides: Record<string, unknown> = {}) => ({
  provider,
  label: provider === "codex" ? "default" : provider.split(":")[1],
  email: `${provider.replace(":", "_")}@example.com`,
  plan: "plus",
  connected_at: "2026-07-31T00:00:00Z",
  ...overrides,
});

type Route = (url: string, init?: RequestInit) => Promise<Response> | undefined;

function stubFetch(route: Route) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const res = route(String(input), init);
    if (res) return res;
    return Promise.reject(new Error(`unstubbed fetch: ${String(input)}`));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("CodexAccountsCard", () => {
  it("renders connected accounts and disconnects after confirmation", async () => {
    let accounts = [account("codex"), account("codex:work", { plan: "pro" })];
    const fetchMock = stubFetch((url, init) => {
      if (url.endsWith("/oauth/accounts") && !init?.method) {
        return Promise.resolve(jsonResponse(accounts));
      }
      if (url.endsWith("/oauth/accounts/codex%3Awork") && init?.method === "DELETE") {
        accounts = accounts.filter((a) => a.provider !== "codex:work");
        return Promise.resolve(jsonResponse({ status: "deleted" }));
      }
      if (url.endsWith("/oauth/codex/flows")) return Promise.resolve(jsonResponse([]));
      return undefined;
    });
    vi.stubGlobal("confirm", vi.fn(() => true));

    render(<CodexAccountsCard client={client} />);

    expect(await screen.findByText("work")).toBeTruthy();
    expect(screen.getByText("codex_work@example.com · codex:work")).toBeTruthy();
    expect(screen.getByText("pro")).toBeTruthy();

    fireEvent.click(screen.getByTitle("disconnect codex:work"));

    await waitFor(() => expect(screen.queryByText("work")).toBeNull());
    const deleteCall = fetchMock.mock.calls.find(([, init]) => init?.method === "DELETE");
    expect(deleteCall?.[0]).toBe("http://test/oauth/accounts/codex%3Awork");
    // Default account is still listed after the refresh.
    expect(screen.getByText("default")).toBeTruthy();
  });

  it("does not disconnect when the confirmation is declined", async () => {
    stubFetch((url) => {
      if (url.endsWith("/oauth/accounts")) {
        return Promise.resolve(jsonResponse([account("codex:work")]));
      }
      if (url.endsWith("/oauth/codex/flows")) return Promise.resolve(jsonResponse([]));
      return undefined;
    });
    vi.stubGlobal("confirm", vi.fn(() => false));

    render(<CodexAccountsCard client={client} />);
    fireEvent.click(await screen.findByTitle("disconnect codex:work"));

    await waitFor(() => expect(screen.getByText("work")).toBeTruthy());
  });

  it("runs the add-account flow: start, poll, authorized, refresh accounts", async () => {
    let accounts: ReturnType<typeof account>[] = [];
    let polls = 0;
    const fetchMock = stubFetch((url, init) => {
      if (url.endsWith("/oauth/accounts")) return Promise.resolve(jsonResponse(accounts));
      if (url.endsWith("/oauth/codex/flows")) return Promise.resolve(jsonResponse([]));
      if (url.endsWith("/oauth/codex/start") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
            flow_id: "flow-1",
            verification_url: "https://auth.openai.com/codex/device",
            user_code: "ABCD-1234",
            expires_in: 900,
          }),
        );
      }
      if (url.endsWith("/oauth/codex/flows/flow-1")) {
        polls += 1;
        return Promise.resolve(
          jsonResponse(
            polls < 2
              ? { flow_id: "flow-1", state: "pending" }
              : { flow_id: "flow-1", state: "authorized", account_label: "work" },
          ),
        );
      }
      return undefined;
    });

    render(<CodexAccountsCard client={client} pollMs={10} />);
    await screen.findByText("No codex accounts connected.");

    fireEvent.change(screen.getByTitle("new account label"), { target: { value: "work" } });
    fireEvent.click(screen.getByText("Add account"));

    // Verification link and user code are shown while pending.
    expect(await screen.findByText("ABCD-1234")).toBeTruthy();
    const link = screen.getByRole("link");
    expect(link.getAttribute("href")).toBe("https://auth.openai.com/codex/device");
    expect(link.getAttribute("target")).toBe("_blank");

    // Start request carried the label.
    const startCall = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith("/oauth/codex/start"),
    );
    expect(JSON.parse(String(startCall?.[1]?.body))).toEqual({ label: "work" });

    // Poll reaches authorized: success notice + accounts refetch shows the account.
    accounts = [account("codex:work")];
    expect(await screen.findByText("Connected work")).toBeTruthy();
    await waitFor(() => expect(screen.getByText("codex_work@example.com · codex:work")).toBeTruthy());
  });

  it("shows the error reason and a retry button when the flow errors", async () => {
    stubFetch((url, init) => {
      if (url.endsWith("/oauth/accounts")) return Promise.resolve(jsonResponse([]));
      if (url.endsWith("/oauth/codex/flows") && !init?.method) {
        return Promise.resolve(jsonResponse([]));
      }
      if (url.endsWith("/oauth/codex/start")) {
        return Promise.resolve(
          jsonResponse({
            flow_id: "flow-err",
            verification_url: "https://example.com/device",
            user_code: "WXYZ-9999",
            expires_in: 900,
          }),
        );
      }
      if (url.endsWith("/oauth/codex/flows/flow-err")) {
        return Promise.resolve(
          jsonResponse({ flow_id: "flow-err", state: "error", error: "exchange failed" }),
        );
      }
      return undefined;
    });

    render(<CodexAccountsCard client={client} pollMs={10} />);
    await screen.findByText("No codex accounts connected.");
    fireEvent.click(screen.getByText("Add account"));

    expect(await screen.findByText("Login failed: exchange failed")).toBeTruthy();
    expect(screen.getByTitle("retry flow flow-err")).toBeTruthy();
  });

  it("shows an expired notice when the flow times out", async () => {
    stubFetch((url, init) => {
      if (url.endsWith("/oauth/accounts")) return Promise.resolve(jsonResponse([]));
      if (url.endsWith("/oauth/codex/flows") && !init?.method) {
        return Promise.resolve(jsonResponse([]));
      }
      if (url.endsWith("/oauth/codex/start")) {
        return Promise.resolve(
          jsonResponse({
            flow_id: "flow-exp",
            verification_url: "https://example.com/device",
            user_code: "PQRS-1111",
            expires_in: 900,
          }),
        );
      }
      if (url.endsWith("/oauth/codex/flows/flow-exp")) {
        return Promise.resolve(jsonResponse({ flow_id: "flow-exp", state: "expired" }));
      }
      return undefined;
    });

    render(<CodexAccountsCard client={client} pollMs={10} />);
    await screen.findByText("No codex accounts connected.");
    fireEvent.click(screen.getByText("Add account"));

    expect(
      await screen.findByText("Login flow expired before authorization."),
    ).toBeTruthy();
    expect(screen.getByTitle("retry flow flow-exp")).toBeTruthy();
  });

  it("resumes showing a pending flow recovered from the flows list on mount", async () => {
    let cancelled = false;
    stubFetch((url) => {
      if (url.endsWith("/oauth/accounts")) return Promise.resolve(jsonResponse([]));
      if (url.endsWith("/oauth/codex/flows/flow-pending/cancel")) {
        cancelled = true;
        return Promise.resolve(jsonResponse({ status: "cancelled" }));
      }
      if (url.endsWith("/oauth/codex/flows/flow-pending")) {
        return Promise.resolve(
          jsonResponse({
            flow_id: "flow-pending",
            state: cancelled ? "cancelled" : "pending",
          }),
        );
      }
      if (url.endsWith("/oauth/codex/flows")) {
        return Promise.resolve(
          jsonResponse([
            {
              flow_id: "flow-pending",
              state: "pending",
              verification_url: "https://example.com/device",
              user_code: "RECO-VER1",
              created_at: "2026-07-31T00:00:00Z",
            },
          ]),
        );
      }
      return undefined;
    });

    render(<CodexAccountsCard client={client} pollMs={10} />);

    expect(await screen.findByText("RECO-VER1")).toBeTruthy();
    fireEvent.click(screen.getByTitle("cancel flow flow-pending"));
    await waitFor(() => expect(cancelled).toBe(true));
    await waitFor(() => expect(screen.queryByText("RECO-VER1")).toBeNull());
  });

  it("degrades to a not-supported note when the server lacks the endpoints", async () => {
    stubFetch((url) => {
      if (url.includes("/oauth/")) {
        return Promise.resolve(new Response("not found", { status: 404 }));
      }
      return undefined;
    });

    render(<CodexAccountsCard client={client} />);

    expect(
      await screen.findByText("Codex OAuth is not supported by this server."),
    ).toBeTruthy();
  });
});
