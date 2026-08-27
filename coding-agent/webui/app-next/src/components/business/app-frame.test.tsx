import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import zhMessages from "../../../messages/zh.json";
import {
  fakeServices,
  render,
  resolveCatalog,
  resolveSnapshot,
  withIntl,
} from "../../../test/helpers/app-frame";
import { flush, makeSnapshot, waitUntil, FakeBackend } from "../../../test/helpers/connected-chat-fake";
import { AppFrame, AppFrameView } from "@/components/business/app-frame";
import type { RuntimeConfigPatch } from "@/lib/connected-chat/client";
import { ChatApiError } from "@/lib/connected-chat/client";
import { SETTINGS_LS_KEY } from "@/lib/session-settings";

// AppFrame reads ?session= via next/navigation; jsdom has no Next router, so
// the navigation hooks are mocked. `nav` is mutable on purpose: tests for the
// details lifecycle change the RAW session param and rerender (02 §4.2).
const nav = vi.hoisted(() => ({ session: null as string | null, pushes: [] as string[] }));

vi.mock("next/navigation", () => ({
  useSearchParams: () =>
    new URLSearchParams(nav.session === null ? "" : `session=${nav.session}`),
  usePathname: () => "/",
  useRouter: () => ({
    push: (url: string) => {
      nav.pushes.push(url);
    },
    replace: () => {},
    prefetch: () => {},
  }),
}));

function renderShell() {
  const { backend, controller, services } = fakeServices();
  // The catalog list stays pending: these tests pin the shell hierarchy and
  // the details lifecycle, not connected catalog data (that lives in
  // connected-chat.test.tsx).
  const utils = render(withIntl(<AppFrame services={services} />));
  return { backend, controller, services, ...utils };
}

function attachSettingsClient(
  backend: FakeBackend,
  updateRuntimeConfig = vi.fn(
    async (sessionId: string, patch: RuntimeConfigPatch) => ({
      session_id: sessionId,
      provider_name: patch.provider ?? null,
      model_name: patch.model ?? null,
      base_url: patch.base_url ?? null,
    }),
  ),
) {
  Object.assign(backend, {
    listProviderModels: vi.fn(async (provider: string) => ({
      provider,
      source: "unavailable" as const,
      models: [],
    })),
    listOAuthAccounts: vi.fn(async () => []),
    listCodexFlows: vi.fn(async () => []),
    startCodexOAuth: vi.fn(),
    getCodexFlow: vi.fn(),
    cancelCodexFlow: vi.fn(),
    deleteOAuthAccount: vi.fn(),
    updateRuntimeConfig,
  });
  return updateRuntimeConfig;
}

function detailsToggle() {
  return screen.getByTitle(zhMessages.sessionbar.detailsToggle);
}

function settingsToggle(container: HTMLElement): HTMLButtonElement {
  const button = Array.from(container.querySelectorAll<HTMLButtonElement>(".sessionbar button")).find(
    (candidate) => candidate.getAttribute("aria-label") === zhMessages.sessionbar.settings,
  );
  if (!button) throw new Error("missing SessionBar settings button");
  return button;
}

function stubMidTier(matches: boolean) {
  const original = window.matchMedia;
  window.matchMedia = ((query: string) => ({
    matches,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as typeof window.matchMedia;
  return () => {
    window.matchMedia = original;
  };
}

afterEach(() => {
  nav.session = null;
  nav.pushes.length = 0;
  localStorage.clear();
});

describe("AppFrame shell (02 §2)", () => {
  it("renders the four regions in the exact .appframe hierarchy", () => {
    const { container } = renderShell();

    const appframe = container.querySelector(".appframe");
    expect(appframe).not.toBeNull();
    const children = Array.from(appframe?.children ?? []).map(
      (element) => element.className.split(" ")[0],
    );
    expect(children).toEqual(["rail", "sidebar", "conversation", "details"]);

    const rail = container.querySelector(".rail");
    const sidebar = container.querySelector(".sidebar");
    const conversation = container.querySelector(".conversation");
    const details = container.querySelector(".details");

    expect(rail?.tagName).toBe("NAV");
    expect(sidebar?.tagName).toBe("ASIDE");
    expect(conversation?.tagName).toBe("MAIN");
    expect(details?.tagName).toBe("ASIDE");

    // SessionBar is a single header row; timeline and composer keep their
    // exact slots inside the conversation column.
    expect(conversation?.querySelector(":scope > header.sessionbar")).not.toBeNull();
    expect(conversation?.querySelector(":scope > .timeline-scroll")).not.toBeNull();
    expect(conversation?.querySelector(":scope > .composer-slot")).not.toBeNull();
  });

  it("has exactly three vertical scroll regions (02 §2)", () => {
    const { container } = renderShell();

    expect(container.querySelector(".session-list")).not.toBeNull();
    expect(container.querySelector(".timeline-scroll")).not.toBeNull();
    expect(container.querySelector(".details-scroll")).not.toBeNull();
    // The details pane has exactly one scroll container (no per-section scroll).
    expect(container.querySelectorAll(".details .details-scroll")).toHaveLength(1);
  });

  it("renders a safe loading shell without services (static prerender parity)", () => {
    // AppFrameView with no ConnectedChatProvider above it is exactly what the
    // static export prerender produces: no services, no selection, no fetch —
    // and the frame must still render its regions and loading states.
    const { container } = render(withIntl(<AppFrameView />));

    expect(screen.getByText(zhMessages.sidebar.loading)).toBeDefined();
    expect(screen.getByText(zhMessages.timeline.loading)).toBeDefined();
    // Static (inert) composer: no editable prompt textarea without a live
    // selection (the sidebar search input keeps its own textbox role).
    expect(
      screen.queryByRole("textbox", { name: zhMessages.composer.inputLabel }),
    ).toBeNull();
    // Rail dot stays the neutral static placeholder (no health attribute).
    const dot = container.querySelector(".rail-dot");
    expect(dot?.getAttribute("aria-label")).toBe(zhMessages.rail.health);
    expect(dot?.getAttribute("data-health")).toBeNull();
    // Regions and details contract survive the loading state.
    expect(container.querySelector(".session-list")).not.toBeNull();
    expect(container.querySelector(".timeline-scroll")).not.toBeNull();
    const details = container.querySelector(".details");
    expect(details?.classList.contains("closed")).toBe(true);
    expect(details?.querySelector(".details-scroll")).not.toBeNull();
  });
});

describe("AppFrame runtime settings", () => {
  it("does not reuse an applied API key after a provider change without a key", async () => {
    const { backend, services } = fakeServices();
    const updateRuntimeConfig = attachSettingsClient(backend);
    const createSession = vi.spyOn(backend, "createSession");
    const { container } = render(withIntl(<AppFrame services={services} />));
    await resolveCatalog(backend, [
      {
        session_id: "session-01",
        title: "Existing session",
        provider_name: "anthropic",
        model_name: "claude-sonnet-4",
      },
    ]);

    const settingsButton = settingsToggle(container);
    fireEvent.click(settingsButton);
    fireEvent.change(screen.getByLabelText(zhMessages.settings.apiKey), {
      target: { value: "sk-old-secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: zhMessages.settings.apply }));
    await waitFor(() => expect(updateRuntimeConfig).toHaveBeenCalledTimes(1));

    fireEvent.click(settingsButton);
    fireEvent.click(settingsButton);
    fireEvent.change(screen.getByLabelText(zhMessages.settings.provider), {
      target: { value: "deepseek" },
    });
    fireEvent.change(screen.getByLabelText(zhMessages.settings.model), {
      target: { value: "deepseek-chat" },
    });
    fireEvent.click(screen.getByRole("button", { name: zhMessages.settings.apply }));
    await waitFor(() => expect(updateRuntimeConfig).toHaveBeenCalledTimes(2));

    fireEvent.click(screen.getByRole("button", { name: zhMessages.sidebar.newSession }));
    // New Session only opens a local draft; the POST fires on the first send.
    expect(createSession).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole("textbox", { name: zhMessages.composer.inputLabel }), {
      target: { value: "Hello" },
    });
    fireEvent.click(screen.getByRole("button", { name: zhMessages.composer.send }));
    await waitFor(() => expect(createSession).toHaveBeenCalled());

    expect(createSession.mock.calls.at(-1)?.[0]).toEqual({
      provider: "deepseek",
      model: "deepseek-chat",
    });
  });

  it("keeps New Session defaults unchanged when runtime Apply fails", async () => {
    const { backend, services } = fakeServices();
    const updateRuntimeConfig = attachSettingsClient(
      backend,
      vi.fn(async () => {
        throw new Error("runtime PATCH failed");
      }),
    );
    const createSession = vi.spyOn(backend, "createSession");
    const { container } = render(withIntl(<AppFrame services={services} />));
    await resolveCatalog(backend, [
      {
        session_id: "session-01",
        title: "Existing session",
        provider_name: "anthropic",
        model_name: "claude-sonnet-4",
      },
    ]);

    fireEvent.click(settingsToggle(container));
    fireEvent.change(screen.getByLabelText(zhMessages.settings.provider), {
      target: { value: "deepseek" },
    });
    fireEvent.change(screen.getByLabelText(zhMessages.settings.model), {
      target: { value: "deepseek-chat" },
    });
    fireEvent.click(screen.getByRole("button", { name: zhMessages.settings.apply }));
    await waitFor(() => expect(updateRuntimeConfig).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toContain(
        zhMessages.settings.saveFailed,
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: zhMessages.sidebar.newSession }));
    fireEvent.change(screen.getByRole("textbox", { name: zhMessages.composer.inputLabel }), {
      target: { value: "Hello" },
    });
    fireEvent.click(screen.getByRole("button", { name: zhMessages.composer.send }));
    await waitFor(() => expect(createSession).toHaveBeenCalled());

    expect(createSession.mock.calls.at(-1)?.[0]).toEqual({
      provider: "anthropic",
      model: "claude-sonnet-4",
    });
  });

  it("keeps tape-rebind defaults for the next New Session", async () => {
    const { backend, services } = fakeServices();
    const updateRuntimeConfig = attachSettingsClient(
      backend,
      vi.fn(async () => {
        throw new ChatApiError(500, {
          code: "http_error",
          message: "session tape target cannot be rebound",
          retryable: false,
        });
      }),
    );
    const createSession = vi.spyOn(backend, "createSession");
    const { container } = render(withIntl(<AppFrame services={services} />));
    await resolveCatalog(backend, [
      {
        session_id: "session-01",
        title: "Existing session",
        provider_name: "anthropic",
        model_name: "claude-sonnet-4",
      },
    ]);

    fireEvent.click(settingsToggle(container));
    fireEvent.change(screen.getByLabelText(zhMessages.settings.provider), {
      target: { value: "deepseek" },
    });
    fireEvent.change(screen.getByLabelText(zhMessages.settings.model), {
      target: { value: "deepseek-chat" },
    });
    fireEvent.click(screen.getByRole("button", { name: zhMessages.settings.apply }));
    await waitFor(() => expect(updateRuntimeConfig).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toContain(zhMessages.settings.tapeRebound),
    );

    fireEvent.click(screen.getByRole("button", { name: zhMessages.sidebar.newSession }));
    fireEvent.change(screen.getByRole("textbox", { name: zhMessages.composer.inputLabel }), {
      target: { value: "Hello" },
    });
    fireEvent.click(screen.getByRole("button", { name: zhMessages.composer.send }));
    await waitFor(() => expect(createSession).toHaveBeenCalled());

    expect(createSession.mock.calls.at(-1)?.[0]).toEqual({
      provider: "deepseek",
      model: "deepseek-chat",
    });
  });

  it("resolves bare codex to the connected labeled account on New Session", async () => {
    localStorage.setItem(
      SETTINGS_LS_KEY,
      JSON.stringify({ provider: "codex", model: "gpt-5.4", base_url: "" }),
    );
    const { backend, services } = fakeServices();
    attachSettingsClient(backend);
    const listOAuthAccounts = vi.fn(async () => [
      { provider: "codex:kina0630test-gmail-com", label: "kina" },
    ]);
    Object.assign(backend, { listOAuthAccounts });
    const createSession = vi.spyOn(backend, "createSession");
    render(withIntl(<AppFrame services={services} />));
    await resolveCatalog(backend, [
      {
        session_id: "session-01",
        title: "Existing session",
        provider_name: "anthropic",
        model_name: "claude-sonnet-4",
      },
    ]);

    await waitFor(() => expect(listOAuthAccounts).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: zhMessages.sidebar.newSession }));
    fireEvent.change(screen.getByRole("textbox", { name: zhMessages.composer.inputLabel }), {
      target: { value: "Hello" },
    });
    fireEvent.click(screen.getByRole("button", { name: zhMessages.composer.send }));

    await waitFor(() => expect(createSession).toHaveBeenCalled());
    expect(createSession.mock.calls.at(-1)?.[0]).toEqual({
      provider: "codex:kina0630test-gmail-com",
      model: "gpt-5.4",
    });
  });
  it("uses the connected Codex live default when fallback persistence races account discovery", async () => {
    const accountProvider = "codex:night-owl";
    let resolveAccounts!: (
      accounts: Array<{ provider: string; label: string }>,
    ) => void;
    const accountsPromise = new Promise<Array<{ provider: string; label: string }>>((resolve) => {
      resolveAccounts = resolve;
    });
    const { backend, services } = fakeServices();
    attachSettingsClient(backend);
    const listProviderModels = vi.fn(async (provider: string) => ({
      provider,
      source: "live" as const,
      models:
        provider === accountProvider
          ? ["gpt-5.6-sol", "gpt-5.4"]
          : ["claude-sonnet-4"],
    }));
    Object.assign(backend, {
      listOAuthAccounts: vi.fn(() => accountsPromise),
      listProviderModels,
    });
    const createSession = vi.spyOn(backend, "createSession");
    render(withIntl(<AppFrame services={services} />));
    await resolveCatalog(backend, []);

    await waitFor(() =>
      expect(
        listProviderModels.mock.calls.some(([provider]) => provider === "anthropic"),
      ).toBe(true),
    );
    fireEvent.click(screen.getByRole("button", { name: zhMessages.sidebar.newSession }));
    fireEvent.click(
      await screen.findByRole("button", { name: "claude-sonnet-4 · anthropic" }),
    );
    fireEvent.click(screen.getByRole("option", { name: "claude-sonnet-4" }));
    await waitFor(() => expect(localStorage.getItem(SETTINGS_LS_KEY)).not.toBeNull());

    await act(async () => {
      resolveAccounts([{ provider: accountProvider, label: "Night Owl" }]);
      await flush();
    });

    expect(
      await screen.findByRole("button", { name: /gpt-5\.6-sol.*Night Owl/ }),
    ).toBeDefined();
    fireEvent.change(screen.getByRole("textbox", { name: zhMessages.composer.inputLabel }), {
      target: { value: "Hello" },
    });
    fireEvent.click(screen.getByRole("button", { name: zhMessages.composer.send }));

    await waitFor(() => expect(createSession).toHaveBeenCalled());
    expect(createSession.mock.calls.at(-1)?.[0]).toEqual({
      provider: accountProvider,
      model: "gpt-5.6-sol",
    });
  });

  it("does not replace a non-default choice made while account discovery is pending", async () => {
    const accountProvider = "codex:night-owl";
    let resolveAccounts!: (
      accounts: Array<{ provider: string; label: string }>,
    ) => void;
    const accountsPromise = new Promise<Array<{ provider: string; label: string }>>((resolve) => {
      resolveAccounts = resolve;
    });
    const { backend, services } = fakeServices();
    attachSettingsClient(backend);
    const listProviderModels = vi.fn(async (provider: string) => ({
      provider,
      source: "live" as const,
      models:
        provider === accountProvider
          ? ["gpt-5.6-sol"]
          : provider === "deepseek"
            ? ["deepseek-chat"]
            : ["claude-sonnet-4"],
    }));
    Object.assign(backend, {
      listOAuthAccounts: vi.fn(() => accountsPromise),
      listProviderModels,
    });
    render(withIntl(<AppFrame services={services} />));
    await resolveCatalog(backend, []);

    fireEvent.click(screen.getByRole("button", { name: zhMessages.sidebar.newSession }));
    fireEvent.click(
      await screen.findByRole("button", { name: "claude-sonnet-4 · anthropic" }),
    );
    fireEvent.click(screen.getByRole("option", { name: "deepseek" }));
    fireEvent.click(await screen.findByRole("option", { name: "deepseek-chat" }));
    await waitFor(() =>
      expect(localStorage.getItem(SETTINGS_LS_KEY)).toContain("deepseek-chat"),
    );

    await act(async () => {
      resolveAccounts([{ provider: accountProvider, label: "Night Owl" }]);
      await flush();
    });

    expect(
      await screen.findByRole("button", { name: "deepseek-chat · deepseek" }),
    ).toBeDefined();
    expect(
      listProviderModels.mock.calls.some(([provider]) => provider === accountProvider),
    ).toBe(false);
  });

});

describe("AppFrame composer model chip", () => {
  it("changes the session model from the chip left of Send", async () => {
    const { backend, services } = fakeServices();
    const updateRuntimeConfig = attachSettingsClient(backend);
    Object.assign(backend, {
      listProviderModels: vi.fn(async (provider: string) => ({
        provider,
        source: "live" as const,
        models: ["claude-sonnet-4", "claude-opus-4"],
      })),
    });
    const { container } = render(withIntl(<AppFrame services={services} />));
    await resolveCatalog(backend, [
      {
        session_id: "session-01",
        title: "Existing session",
        provider_name: "anthropic",
        model_name: "claude-sonnet-4",
      },
    ]);
    await resolveSnapshot(backend, "session-01");

    const chip = await waitFor(() =>
      screen.getByRole("button", { name: "claude-sonnet-4 · anthropic" }),
    );
    const toolbar = container.querySelector(".composer-toolbar");
    const send = screen.getByRole("button", { name: zhMessages.composer.send });
    expect(toolbar?.contains(chip)).toBe(true);
    expect(toolbar?.contains(send)).toBe(true);
    expect(container.querySelector(".composer label")).toBeNull();

    fireEvent.click(chip);
    await waitFor(() => expect(screen.getByRole("option", { name: "claude-opus-4" })).toBeDefined());
    fireEvent.click(screen.getByRole("option", { name: "claude-opus-4" }));
    await waitFor(() => expect(updateRuntimeConfig).toHaveBeenCalled());
    expect(updateRuntimeConfig.mock.calls.at(-1)?.[1]).toMatchObject({ model: "claude-opus-4" });
  });
});

describe("AppFrame conversation tabs", () => {
  it("splits the conversation column into Chat and Trajectory views", () => {
    const { container } = renderShell();
    const chatTab = screen.getByRole("tab", { name: zhMessages.conversation.chat });
    const trajectoryTab = screen.getByRole("tab", { name: zhMessages.conversation.trajectory });
    expect(chatTab.getAttribute("aria-selected")).toBe("true");
    expect(container.querySelector(".timeline")).not.toBeNull();

    fireEvent.click(trajectoryTab);
    expect(trajectoryTab.getAttribute("aria-selected")).toBe("true");
    expect(container.querySelector(".trajectory")).not.toBeNull();
    expect(container.querySelector(".conversation > .timeline-scroll")).not.toBeNull();
  });
});

describe("AppFrame details lifecycle (02 §4)", () => {
  it("details pane is closed by default and stays mounted but inert (02 §4)", () => {
    const { container } = renderShell();

    const details = container.querySelector(".details");
    expect(details).not.toBeNull();
    expect(details?.classList.contains("closed")).toBe(true);
    expect(details?.getAttribute("aria-hidden")).toBe("true");
    expect(details?.hasAttribute("inert")).toBe(true);
    // Collapsed, NOT unmounted: the scroll subtree is still in the DOM.
    expect(details?.querySelector(".details-scroll")).not.toBeNull();

    const toggle = detailsToggle();
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(toggle.getAttribute("aria-controls")).toBe("details-pane");
    expect(details?.id).toBe("details-pane");
  });

  it("opens the details pane via the SessionBar toggle", () => {
    const { container } = renderShell();

    fireEvent.click(detailsToggle());

    const details = container.querySelector(".details");
    expect(details?.classList.contains("closed")).toBe(false);
    expect(details?.getAttribute("aria-hidden")).toBe("false");
    expect(details?.hasAttribute("inert")).toBe(false);
    expect(detailsToggle().getAttribute("aria-expanded")).toBe("true");
  });

  it("Esc closes the pane only when focus is inside, then returns focus to the toggle (02 §4.5)", () => {
    const { container } = renderShell();

    fireEvent.click(detailsToggle());
    const details = container.querySelector(".details") as HTMLElement;

    // Esc with focus OUTSIDE the pane never closes it.
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(details.classList.contains("closed")).toBe(false);

    // Focus inside the pane: Esc closes and focus returns to the toggle.
    details.tabIndex = -1;
    details.focus();
    expect(document.activeElement).toBe(details);
    fireEvent.keyDown(details, { key: "Escape" });

    expect(details.classList.contains("closed")).toBe(true);
    expect(document.activeElement).toBe(detailsToggle());
  });

  it("mid-tier click-away closes the pane from the two work surfaces only (02 §4.2)", () => {
    const restore = stubMidTier(true);
    try {
      const { container } = renderShell();

      // Click-away from .timeline-scroll closes.
      fireEvent.click(detailsToggle());
      fireEvent.click(container.querySelector(".timeline-scroll") as HTMLElement);
      expect(container.querySelector(".details")?.classList.contains("closed")).toBe(true);

      // Click-away from .composer-slot closes.
      fireEvent.click(detailsToggle());
      fireEvent.click(container.querySelector(".composer-slot") as HTMLElement);
      expect(container.querySelector(".details")?.classList.contains("closed")).toBe(true);

      // The SessionBar (incl. the toggle surface) never closes the pane.
      fireEvent.click(detailsToggle());
      fireEvent.click(container.querySelector(".sessionbar") as HTMLElement);
      expect(container.querySelector(".details")?.classList.contains("closed")).toBe(false);

      // Clicking inside the details pane itself never closes it.
      fireEvent.click(container.querySelector(".details") as HTMLElement);
      expect(container.querySelector(".details")?.classList.contains("closed")).toBe(false);
    } finally {
      restore();
    }
  });

  it("click-away is inert outside the mid-tier overlay band (02 §4.2)", () => {
    // Default test stub reports matches:false (below the mid-tier band).
    const { container } = renderShell();

    fireEvent.click(detailsToggle());
    fireEvent.click(container.querySelector(".timeline-scroll") as HTMLElement);
    fireEvent.click(container.querySelector(".composer-slot") as HTMLElement);

    expect(container.querySelector(".details")?.classList.contains("closed")).toBe(false);
  });

  it("closes details when the raw ?session= param becomes absent (02 §4.2)", () => {
    nav.session = "session-x";
    const { container, rerender, services } = renderShell();

    fireEvent.click(detailsToggle());
    expect(container.querySelector(".details")?.classList.contains("closed")).toBe(false);

    // The close effect fires on EVERY raw query-parameter change, independent
    // of how the selection normalizes afterwards.
    nav.session = null;
    rerender(withIntl(<AppFrame services={services} />));

    const details = container.querySelector(".details");
    expect(details?.classList.contains("closed")).toBe(true);
    expect(details?.getAttribute("aria-hidden")).toBe("true");
    expect(detailsToggle().getAttribute("aria-expanded")).toBe("false");
  });

  it("closes details when the raw ?session= param changes value (02 §4.2)", () => {
    nav.session = "session-x";
    const { container, rerender, services } = renderShell();

    fireEvent.click(detailsToggle());
    expect(container.querySelector(".details")?.classList.contains("closed")).toBe(false);

    nav.session = "no-such-session";
    rerender(withIntl(<AppFrame services={services} />));

    const details = container.querySelector(".details");
    expect(details?.classList.contains("closed")).toBe(true);
    expect(details?.getAttribute("aria-hidden")).toBe("true");
    expect(detailsToggle().getAttribute("aria-expanded")).toBe("false");
  });
});

describe("AppFrame draft sessions", () => {
  it("New Session opens a local draft: live composer, no POST, no new row", async () => {
    const { backend, services } = fakeServices();
    const { container } = render(withIntl(<AppFrame services={services} />));
    await resolveCatalog(backend, [
      { session_id: "session-01", title: "Existing session" },
    ]);

    fireEvent.click(screen.getByRole("button", { name: zhMessages.sidebar.newSession }));

    expect(backend.creates).toHaveLength(0);
    expect(nav.pushes).toHaveLength(0);
    // The composer is live immediately, bound to the local draft.
    expect(
      screen.getByRole("textbox", { name: zhMessages.composer.inputLabel }),
    ).toBeDefined();
    // No catalog row is added and no existing row stays selected.
    expect(container.querySelectorAll(".session")).toHaveLength(1);
    expect(container.querySelector(".session.sel")).toBeNull();
  });

  it("hides catalog rows without a title and never shows the session id", async () => {
    const { backend, services } = fakeServices();
    const { container } = render(withIntl(<AppFrame services={services} />));
    await resolveCatalog(backend, [
      { session_id: "session-01", title: "Existing session" },
      { session_id: "9f8b7c6d-untitled-uuid", title: null },
    ]);

    const list = container.querySelector(".session-list");
    expect(container.querySelectorAll(".session")).toHaveLength(1);
    expect(list?.textContent).toContain("Existing session");
    expect(list?.textContent).not.toContain("9f8b7c6d-untitled-uuid");
  });

  it("shows provider · model as the row meta instead of the session id", async () => {
    const { backend, services } = fakeServices();
    const { container } = render(withIntl(<AppFrame services={services} />));
    await resolveCatalog(backend, [
      {
        session_id: "session-01",
        title: "Existing session",
        provider_name: "anthropic",
        model_name: "claude-sonnet-4",
      },
    ]);

    const row = container.querySelector(".session");
    expect(row?.textContent).toContain("anthropic · claude-sonnet-4");
    expect(row?.textContent).not.toContain("session-01");
  });

  it("keeps the first-send session visible and selected while the catalog omits it", async () => {
    const { backend, services } = fakeServices();
    const { container } = render(withIntl(<AppFrame services={services} />));
    await resolveCatalog(backend, []);

    fireEvent.click(screen.getByRole("button", { name: zhMessages.sidebar.newSession }));
    fireEvent.change(screen.getByRole("textbox", { name: zhMessages.composer.inputLabel }), {
      target: { value: "Refactor the shell" },
    });
    fireEvent.click(screen.getByRole("button", { name: zhMessages.composer.send }));

    // POST /sessions happens on the first send, not on New Session.
    await waitUntil(() => backend.creates.length === 1, "first send to create the session");
    await act(async () => {
      backend.creates[0].resolve({ session_id: "session-09" });
      await flush();
    });
    await act(async () => {
      backend.lists.at(-1)?.resolve({
        contract_version: "1.0.0",
        sessions: [],
      });
      await flush();
    });

    // The flow navigates and selects the created session before prompting.
    await waitUntil(
      () => backend.snapshotCalls.length === 1,
      "draft flow to select the created session",
    );
    expect(nav.pushes).toContain("/?session=session-09");
    expect(backend.snapshotCalls[0].sessionId).toBe("session-09");

    // Live GET /sessions omits the untitled session. Its local stand-in stays
    // selected, and the conversation remains bound to the new controller state.
    await waitFor(() =>
      expect(container.querySelector(".session.sel")?.textContent).toContain(
        "Refactor the shell",
      ),
    );
    expect(container.querySelector(".session.sel")?.textContent).not.toContain("session-09");
    expect(container.querySelector(".sessionbar-title")?.textContent).toBe(
      "Refactor the shell",
    );
    expect(
      screen.getByRole("textbox", { name: zhMessages.composer.inputLabel }),
    ).toBeDefined();
    expect(screen.getByText(zhMessages.timeline.loading)).toBeDefined();

    // Once the snapshot lands, the prompt is sent.
    await act(async () => {
      backend.snapshots[0].resolve(makeSnapshot("session-09", []));
      await flush();
    });
    await waitUntil(() => backend.promptCalls.length === 1, "draft flow to send the first prompt");
    expect(backend.promptCalls[0].request.prompt).toBe("Refactor the shell");
  });

  it("keeps the draft when session creation fails", async () => {
    const { backend, services } = fakeServices();
    render(withIntl(<AppFrame services={services} />));
    await resolveCatalog(backend, []);

    fireEvent.click(screen.getByRole("button", { name: zhMessages.sidebar.newSession }));
    fireEvent.change(screen.getByRole("textbox", { name: zhMessages.composer.inputLabel }), {
      target: { value: "Refactor the shell" },
    });
    fireEvent.click(screen.getByRole("button", { name: zhMessages.composer.send }));
    await waitUntil(() => backend.creates.length === 1, "first send to create the session");
    await act(async () => {
      backend.creates[0].reject(new Error("provider unavailable"));
      await flush();
    });

    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain("provider unavailable"),
    );
    expect(backend.lists).toHaveLength(1);
    // The draft survives the failure so the user can retry.
    expect(
      (screen.getByRole("textbox", { name: zhMessages.composer.inputLabel }) as HTMLTextAreaElement)
        .value,
    ).toBe("Refactor the shell");
  });
});
