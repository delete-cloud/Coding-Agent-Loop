// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ProfileStore } from "../lib/profiles";
import ConnectionPanel from "./ConnectionPanel";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const jsonResponse = (body: unknown) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });

const twoProfiles = (): ProfileStore => ({
  profiles: [
    { id: "a", name: "local", baseUrl: "http://localhost:8080", apiKey: "" },
    { id: "b", name: "staging", baseUrl: "https://staging.example.com", apiKey: "k" },
  ],
  activeId: "a",
});

function renderPanel(store: ProfileStore, onChange = vi.fn()) {
  const utils = render(
    <div data-connection-root className="relative">
      <ConnectionPanel store={store} onChange={onChange} onClose={() => undefined} />
    </div>,
  );
  return { ...utils, onChange };
}

describe("ConnectionPanel profile list", () => {
  it("marks the active profile and guards its delete button", () => {
    renderPanel(twoProfiles());

    const activeRow = screen.getByText("local").closest("li")!;
    expect((activeRow.querySelector("button[title*='active' i]") as HTMLButtonElement).disabled).toBe(
      true,
    );
    // Inactive profile's delete is enabled.
    const inactiveRow = screen.getByText("staging").closest("li")!;
    const del = Array.from(inactiveRow.querySelectorAll("button")).find(
      (b) => b.textContent === "Delete",
    ) as HTMLButtonElement;
    expect(del.disabled).toBe(false);
  });

  it("activates another profile via Switch", () => {
    const { onChange } = renderPanel(twoProfiles());

    fireEvent.click(screen.getByRole("button", { name: "Switch" }));

    expect(onChange).toHaveBeenCalledWith({ ...twoProfiles(), activeId: "b" });
  });

  it("deletes an inactive profile after confirmation", () => {
    vi.stubGlobal("confirm", vi.fn(() => true));
    const { onChange } = renderPanel(twoProfiles());

    const inactiveRow = screen.getByText("staging").closest("li")!;
    const del = Array.from(inactiveRow.querySelectorAll("button")).find(
      (b) => b.textContent === "Delete",
    )!;
    fireEvent.click(del);

    expect(onChange).toHaveBeenCalledWith({
      profiles: [twoProfiles().profiles[0]],
      activeId: "a",
    });
  });
});

describe("ConnectionPanel new profile form", () => {
  const fillForm = () => {
    fireEvent.click(screen.getByRole("button", { name: "+ New" }));
    fireEvent.change(screen.getByTitle("profile name"), { target: { value: "prod" } });
    fireEvent.change(screen.getByTitle("Server base URL"), {
      target: { value: "https://prod.example.com" },
    });
    fireEvent.change(screen.getByPlaceholderText("X-API-Key"), {
      target: { value: "prod-key" },
    });
  };

  it("tests the connection with the form values and saves after success", async () => {
    let healthUrl = "";
    let healthKey: string | null = null;
    let sessionsKey: string | null = null;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/healthz")) {
          healthUrl = url;
          healthKey = new Headers(init?.headers).get("X-API-Key");
          return Promise.resolve(jsonResponse({ status: "ok", sessions: 4, version: "2.0.1" }));
        }
        if (url.endsWith("/sessions")) {
          sessionsKey = new Headers(init?.headers).get("X-API-Key");
          return Promise.resolve(jsonResponse({ sessions: [] }));
        }
        return Promise.reject(new Error(`unstubbed fetch: ${url}`));
      }),
    );
    const { onChange } = renderPanel(twoProfiles());

    fillForm();
    // Save is disabled before a successful test.
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    expect(await screen.findByText(/^✓ v2\.0\.1 · 4 sessions · \d+ms$/)).toBeTruthy();
    expect(healthUrl).toBe("https://prod.example.com/healthz");
    expect(healthKey).toBe("prod-key");
    // The authenticated probe must carry the form's key too.
    expect(sessionsKey).toBe("prod-key");

    const save = screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;
    expect(save.disabled).toBe(false);
    fireEvent.click(save);

    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0][0] as ProfileStore;
    expect(next.profiles).toHaveLength(3);
    expect(next.profiles[2]).toMatchObject({
      name: "prod",
      baseUrl: "https://prod.example.com",
      apiKey: "prod-key",
    });
    expect(next.activeId).toBe("a");
  });

  it("shows the error on test failure and requires 'save anyway' to save", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response("boom", { status: 500 }))),
    );
    const { onChange } = renderPanel(twoProfiles());

    fillForm();
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    expect(await screen.findByText(/^✗ 500 boom$/)).toBeTruthy();
    const save = screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);

    fireEvent.click(screen.getByRole("checkbox"));
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("invalidates a passing test when a form field changes", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(jsonResponse({ status: "ok", sessions: 0, version: "1.0.0" })),
      ),
    );
    renderPanel(twoProfiles());

    fillForm();
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await screen.findByText(/^✓ v1\.0\.0/);

    fireEvent.change(screen.getByTitle("Server base URL"), {
      target: { value: "https://other.example.com" },
    });
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it("rejects a wrong API key even when healthz passes", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/healthz")) {
          return Promise.resolve(jsonResponse({ status: "ok", sessions: 2, version: "2.0.0" }));
        }
        if (url.endsWith("/sessions")) {
          return Promise.resolve(new Response("invalid api key", { status: 401 }));
        }
        return Promise.reject(new Error(`unstubbed fetch: ${url}`));
      }),
    );
    const { onChange } = renderPanel(twoProfiles());

    fillForm();
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    expect(await screen.findByText(/^✗ API key rejected \(401 invalid api key\)$/)).toBeTruthy();
    const save = screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    // Only 'save anyway' can still persist a profile whose test failed.
    fireEvent.click(screen.getByRole("checkbox"));
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("ignores a test result that resolves after the form values changed", async () => {
    let resolveFetch!: (value: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((res) => {
            resolveFetch = res;
          }),
      ),
    );
    renderPanel(twoProfiles());

    fillForm();
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    // Edit a field while the test request is in flight.
    fireEvent.change(screen.getByPlaceholderText("X-API-Key"), {
      target: { value: "edited-key" },
    });
    const save = screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);

    // The stale success must not unlock Save for values that were never tested.
    resolveFetch(jsonResponse({ status: "ok", sessions: 1, version: "9.9.9" }));
    await new Promise((resolve) => setTimeout(resolve, 0));
    resolveFetch(jsonResponse({ sessions: [] }));
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(save.disabled).toBe(true);
    expect(screen.queryByText(/^✓ /)).toBeNull();
  });
});

describe("ConnectionPanel edit form", () => {
  it("prefills the form and updates the profile on save", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/healthz")) {
          return Promise.resolve(jsonResponse({ status: "ok", sessions: 1, version: "3.1.0" }));
        }
        if (url.endsWith("/sessions")) {
          return Promise.resolve(jsonResponse({ sessions: [] }));
        }
        return Promise.reject(new Error(`unstubbed fetch: ${url}`));
      }),
    );
    const { onChange } = renderPanel(twoProfiles());

    const activeRow = screen.getByText("local").closest("li")!;
    const edit = Array.from(activeRow.querySelectorAll("button")).find(
      (b) => b.textContent === "Edit",
    )!;
    fireEvent.click(edit);

    expect((screen.getByTitle("profile name") as HTMLInputElement).value).toBe("local");
    expect((screen.getByTitle("Server base URL") as HTMLInputElement).value).toBe(
      "http://localhost:8080",
    );

    fireEvent.change(screen.getByTitle("profile name"), { target: { value: "local-dev" } });
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await screen.findByText(/^✓ v3\.1\.0/);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    const next = onChange.mock.calls[0][0] as ProfileStore;
    expect(next.profiles[0]).toMatchObject({ id: "a", name: "local-dev" });
  });
});
