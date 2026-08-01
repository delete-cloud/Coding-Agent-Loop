// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AgentClient } from "../lib/api";
import type { ProfileStore } from "../lib/profiles";
import Header, { PROVIDERS } from "./Header";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const client = new AgentClient({ baseUrl: "http://test" });

const profiles: ProfileStore = {
  profiles: [{ id: "p1", name: "test-server", baseUrl: "http://test", apiKey: "" }],
  activeId: "p1",
};

const jsonResponse = (body: unknown) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });

function renderHeader(provider = "") {
  const config = {
    baseUrl: "http://test",
    apiKey: "",
    repoPath: "",
    approval: "auto" as const,
    provider,
    model: "",
  };
  return render(
    <Header
      config={config}
      onConfigChange={() => undefined}
      profiles={profiles}
      onProfilesChange={() => undefined}
      onNewSession={() => undefined}
      onToggleSidebar={() => undefined}
      sessionId={null}
      status=""
      client={client}
      theme="dark"
      onToggleTheme={() => undefined}
      showThinking={false}
      onToggleThinking={() => undefined}
    />,
  );
}

const providerSelect = () => screen.getByTitle("provider") as HTMLSelectElement;

const optionValues = () =>
  Array.from(providerSelect().querySelectorAll("option")).map((o) => o.value);

describe("Header provider dropdown", () => {
  it("appends connected codex accounts as provider options", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/oauth/accounts")) {
          return Promise.resolve(
            jsonResponse([
              { provider: "codex", label: "default", connected_at: "2026-07-31T00:00:00Z" },
              { provider: "codex:work", label: "work", connected_at: "2026-07-31T00:00:00Z" },
            ]),
          );
        }
        return Promise.reject(new Error(`unstubbed fetch: ${url}`));
      }),
    );

    renderHeader();

    await waitFor(() => expect(optionValues()).toContain("codex:work"));
    // All static providers remain, and the default "codex" key is not duplicated.
    for (const p of PROVIDERS) expect(optionValues()).toContain(p);
    expect(optionValues().filter((v) => v === "codex")).toHaveLength(1);
  });

  it("falls back to the static list when the accounts fetch fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response("not found", { status: 404 }))),
    );

    renderHeader();

    // Give the failed fetch a tick to settle, then verify static-only options.
    await waitFor(() => expect(optionValues()).toContain("codex"));
    expect(optionValues()).toEqual(["", ...PROVIDERS]);
  });

  it("offers codex model presets instead of the kimi/deepseek ones for codex providers", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/oauth/accounts")) {
        return Promise.resolve(
          jsonResponse([
            { provider: "codex:work", label: "work", connected_at: "2026-07-31T00:00:00Z" },
          ]),
        );
      }
      return Promise.reject(new Error(`unstubbed fetch: ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    const { container } = renderHeader("codex:work");

    const values = Array.from(
      container.querySelectorAll<HTMLOptionElement>("#model-options option"),
    ).map((o) => o.value);
    expect(values).toEqual(["gpt-5.5", "gpt-5.4"]);
    // The Responses API has no models endpoint — no live-list fetch may fire.
    await new Promise((r) => setTimeout(r, 300));
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).includes("/providers/")),
    ).toBe(false);
  });
});

describe("Header connection indicator", () => {
  it("shows the active profile name and server version after a successful health check", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/healthz")) {
          return Promise.resolve(jsonResponse({ status: "ok", sessions: 3, version: "1.2.3" }));
        }
        if (url.endsWith("/oauth/accounts")) {
          return Promise.resolve(jsonResponse([]));
        }
        return Promise.reject(new Error(`unstubbed fetch: ${url}`));
      }),
    );

    renderHeader();

    const indicator = screen.getByRole("button", { name: "connection" });
    expect(indicator.textContent).toContain("test-server");
    await waitFor(() => expect(indicator.textContent).toContain("v1.2.3"));
  });

  it("keeps the indicator without a version when the health check fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/healthz")) {
          return Promise.resolve(new Response("down", { status: 502 }));
        }
        if (url.endsWith("/oauth/accounts")) {
          return Promise.resolve(jsonResponse([]));
        }
        return Promise.reject(new Error(`unstubbed fetch: ${url}`));
      }),
    );

    renderHeader();

    const indicator = screen.getByRole("button", { name: "connection" });
    await waitFor(() =>
      expect(indicator.getAttribute("title")).toContain("connection failed"),
    );
    expect(indicator.textContent).not.toMatch(/v\d/);
  });

  it("no longer renders the always-visible base URL and API key inputs", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new Error("offline"))),
    );

    renderHeader();

    expect(screen.queryByTitle("Server base URL")).toBeNull();
    expect(screen.queryByPlaceholderText("X-API-Key")).toBeNull();
    // repo path / provider / model / theme controls are still there.
    expect(screen.getByPlaceholderText(/repo path/i)).toBeTruthy();
    expect(screen.getByTitle("provider")).toBeTruthy();
    expect(screen.getByRole("button", { name: "toggle theme" })).toBeTruthy();
  });
});
