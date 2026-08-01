// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AgentClient } from "../lib/api";
import Header, { PROVIDERS } from "./Header";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const client = new AgentClient({ baseUrl: "http://test" });

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
