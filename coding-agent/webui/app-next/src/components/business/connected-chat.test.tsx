import { act, fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import zhMessages from "../../../messages/zh.json";
import fixture from "../../../test/fixtures/connected-chat/v1/connected-chat-contract.json";
import {
  fakeServices,
  render,
  resolveCatalog,
  resolveSnapshot,
  withIntl,
} from "../../../test/helpers/app-frame";
import {
  chatItem,
  flush,
  makeSnapshot,
  waitUntil,
  type FakeBackend,
} from "../../../test/helpers/connected-chat-fake";
import { AppFrame } from "@/components/business/app-frame";
import { ChatApiError } from "@/lib/connected-chat/client";
import type { ConnectedChatController } from "@/lib/connected-chat/controller";
import type { ChatEventEnvelope } from "@/lib/connected-chat/wire";

// AppFrame-level connected tests: the frame is mounted with injected
// ConnectedChatProvider services (FakeBackend + real controller), so no real
// fetch ever runs. The mocked search params are mutated + rerendered to
// simulate URL-led navigation (user edits, browser back/forward).
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

const events = fixture.events.map((entry) => entry.data as ChatEventEnvelope);

const twoSessions = [
  { session_id: "session-01", title: "Run tests" },
  { session_id: "session-02", title: "Fix lint" },
];

const liveApps: Array<{
  backend: FakeBackend;
  controller: ConnectedChatController;
  unmount: () => void;
}> = [];

function renderConnected() {
  const { backend, controller, services } = fakeServices();
  const utils = render(withIntl(<AppFrame services={services} />));
  const rerenderApp = () => utils.rerender(withIntl(<AppFrame services={services} />));
  liveApps.push({ backend, controller, unmount: utils.unmount });
  return { backend, controller, services, rerenderApp, ...utils };
}

/** Catalog ready with two sessions; session-01 selected and following. */
async function renderFollowing() {
  const app = renderConnected();
  await resolveCatalog(app.backend, twoSessions);
  await resolveSnapshot(app.backend, "session-01");
  return app;
}

function timelineText(container: HTMLElement): string {
  const region = container.querySelector(".timeline-scroll");
  if (region === null) throw new Error("missing .timeline-scroll region");
  return region.textContent ?? "";
}

function railHealth(container: HTMLElement): string | null {
  return container.querySelector(".rail-dot")?.getAttribute("data-health") ?? null;
}

function sessionbarText(container: HTMLElement): string {
  const bar = container.querySelector(".sessionbar");
  if (bar === null) throw new Error("missing .sessionbar");
  return bar.textContent ?? "";
}

function textbox(): HTMLTextAreaElement {
  return screen.getByRole("textbox", {
    name: zhMessages.composer.inputLabel,
  }) as HTMLTextAreaElement;
}

async function settle(work: () => void, ready: () => boolean, description: string) {
  await act(async () => {
    work();
    await flush();
  });
  await waitUntil(ready, description);
}

afterEach(() => {
  nav.session = null;
  nav.pushes.length = 0;
  for (const live of liveApps.splice(0)) {
    live.unmount();
    live.controller.dispose();
    for (const stream of [...live.backend.follows, ...live.backend.prompts, ...live.backend.resumes]) {
      if (!stream.closed) stream.end();
    }
  }
});

describe("AppFrame catalog states", () => {
  it("shows catalog loading in sidebar and timeline before the list resolves", () => {
    const { backend, container } = renderConnected();

    expect(screen.getByText(zhMessages.sidebar.loading)).toBeDefined();
    expect(container.querySelectorAll(".session")).toHaveLength(0);
    expect(screen.getByText(zhMessages.timeline.loading)).toBeDefined();
    // No selection exists yet: the controller must stay untouched.
    expect(backend.snapshotCalls).toHaveLength(0);
    expect(railHealth(container)).toBe("idle");
  });

  it("renders catalog sessions and selects the first one when ?session= is absent", async () => {
    const { backend, container } = renderConnected();
    await resolveCatalog(backend, twoSessions);

    const rows = container.querySelectorAll(".session");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("Run tests");
    expect(rows[0].textContent).not.toContain("session-01");
    expect(rows[1].textContent).toContain("Fix lint");
    expect(rows[1].textContent).not.toContain("session-02");

    const selected = container.querySelector(".session.sel");
    expect(selected?.textContent).toContain("Run tests");
    expect(selected?.getAttribute("aria-current")).toBe("page");

    // URL-led normalization picked the first session and the controller
    // started loading it.
    expect(backend.snapshotCalls.map((call) => call.sessionId)).toEqual(["session-01"]);
    expect(container.querySelector(".sessionbar-title")?.textContent).toBe("Run tests");
  });

  it("hides catalog rows without a title (empty/unsent sessions)", async () => {
    const { backend, container } = renderConnected();
    await resolveCatalog(backend, [
      { session_id: "session-01", title: "Run tests" },
      { session_id: "session-02", title: null },
    ]);

    expect(container.querySelectorAll(".session")).toHaveLength(1);
    const list = container.querySelector(".session-list");
    expect(list?.textContent).toContain("Run tests");
    expect(list?.textContent).not.toContain("session-02");
    // The first visible session is the selection.
    expect(backend.snapshotCalls.map((call) => call.sessionId)).toEqual(["session-01"]);
  });

  it("honors a valid ?session= param as the selection", async () => {
    nav.session = "session-02";
    const { backend, container } = renderConnected();
    await resolveCatalog(backend, twoSessions);

    const selected = container.querySelector(".session.sel");
    expect(selected?.textContent).toContain("Fix lint");
    expect(container.querySelector(".sessionbar-title")?.textContent).toBe("Fix lint");
    expect(backend.snapshotCalls.map((call) => call.sessionId)).toEqual(["session-02"]);
  });

  it("normalizes an invalid ?session= param back to the first session", async () => {
    nav.session = "no-such-session";
    const { backend, container } = renderConnected();
    await resolveCatalog(backend, twoSessions);

    const selected = container.querySelector(".session.sel");
    expect(selected?.textContent).toContain("Run tests");
    expect(backend.snapshotCalls.map((call) => call.sessionId)).toEqual(["session-01"]);
    expect(container.querySelector(".sessionbar-title")?.textContent).toBe("Run tests");
  });

  it("shows the empty states when the catalog has no sessions", async () => {
    const { backend, container } = renderConnected();
    await resolveCatalog(backend, []);

    expect(screen.getByText(zhMessages.sidebar.empty)).toBeDefined();
    expect(timelineText(container)).toContain(zhMessages.timeline.empty);
    // No selection: static inert composer, no controller traffic (the sidebar
    // search input keeps its own textbox role).
    expect(
      screen.queryByRole("textbox", { name: zhMessages.composer.inputLabel }),
    ).toBeNull();
    expect(backend.snapshotCalls).toHaveLength(0);
  });

  it("surfaces a catalog error and recovers through retry", async () => {
    const { backend, container } = renderConnected();
    await settle(
      () => backend.lists[0].reject(new Error("catalog transport down")),
      () => screen.queryByRole("alert") !== null,
      "catalog error alert",
    );

    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain(zhMessages.sidebar.error);

    fireEvent.click(screen.getByRole("button", { name: zhMessages.sidebar.retry }));
    await settle(
      () => {},
      () => backend.lists.length === 2,
      "catalog retry listSessions",
    );
    expect(backend.lists).toHaveLength(2);

    await resolveCatalog(backend, twoSessions);
    expect(container.querySelectorAll(".session")).toHaveLength(2);
  });

  it("keeps an omitted first-send session selected until its catalog title arrives", async () => {
    const { backend, container, controller, rerenderApp } = renderConnected();
    await resolveCatalog(backend, []);

    // New Session is a local draft: no POST until the first prompt is sent.
    fireEvent.click(screen.getByRole("button", { name: zhMessages.sidebar.newSession }));
    expect(backend.creates).toHaveLength(0);

    fireEvent.change(textbox(), { target: { value: "Refactor the shell" } });
    fireEvent.click(screen.getByRole("button", { name: zhMessages.composer.send }));
    await settle(
      () => {},
      () => backend.creates.length === 1,
      "first send to create the session",
    );
    // Pending state switches the button label and disables it.
    expect(
      screen.getByRole("button", { name: zhMessages.sidebar.creating }),
    ).toBeDefined();

    await settle(
      () => backend.creates[0].resolve({ session_id: "session-09" }),
      () => backend.lists.length === 2,
      "create flow to refresh catalog",
    );
    // Live GET /sessions omits the new session until its first prompt has
    // produced a title.
    expect(backend.lists).toHaveLength(2);
    await resolveCatalog(backend, []);

    expect(nav.pushes).toContain("/?session=session-09");
    expect(backend.snapshotCalls.map((call) => call.sessionId)).toEqual(["session-09"]);
    expect(controller.getState().sessionId).toBe("session-09");
    const row = container.querySelector(".session.sel");
    expect(row?.textContent).toContain("Refactor the shell");
    expect(row?.textContent).toContain("anthropic · claude-sonnet-4");
    expect(row?.textContent).not.toContain("session-09");
    expect(container.querySelector(".sessionbar-title")?.textContent).toBe(
      "Refactor the shell",
    );
    expect(textbox().value).toBe("");
    expect(timelineText(container)).toContain(zhMessages.timeline.loading);

    // Simulate the router applying the push while the first prompt finishes.
    nav.session = "session-09";
    rerenderApp();
    await resolveSnapshot(backend, "session-09");
    await waitUntil(() => backend.promptCalls.length === 1, "first prompt stream to start");
    await settle(
      () => backend.prompts[0].end(),
      () => backend.snapshotCalls.length === 2,
      "prompt EOF to request the canonical snapshot",
    );
    await act(async () => {
      backend.snapshots[1].resolve(makeSnapshot("session-09", []));
      await flush();
    });
    await waitUntil(() => backend.lists.length === 3, "completed first send to refresh catalog");
    await act(async () => {
      backend.lists[2].resolve({
        contract_version: "1.1.0",
        sessions: [
          {
            session_id: "session-09",
            title: "Server-generated title",
            provider_name: "codex",
            model_name: "gpt-5.4",
          },
          {
            session_id: "session-01",
            title: "Older session",
            provider_name: "anthropic",
            model_name: "claude-sonnet-4",
          },
        ],
      });
      await flush();
    });

    const titledRow = Array.from(container.querySelectorAll(".session")).find((candidate) =>
      candidate.textContent?.includes("Server-generated title"),
    );
    expect(titledRow?.textContent).toContain("codex · gpt-5.4");
    expect(titledRow?.textContent).not.toContain("Refactor the shell");

    // Once the server title replaces the stand-in, URL navigation is
    // authoritative again.
    nav.session = "session-01";
    rerenderApp();
    await waitUntil(
      () => controller.getState().sessionId === "session-01",
      "titled summary to release the pending selection",
    );
    expect(container.querySelector(".session.sel")?.textContent).toContain("Older session");
  });

  it("returns URL authority after the optimistic pending navigation lands", async () => {
    const { backend, container, controller, rerenderApp } = renderConnected();
    await resolveCatalog(backend, []);

    fireEvent.click(screen.getByRole("button", { name: zhMessages.sidebar.newSession }));
    fireEvent.change(textbox(), { target: { value: "Refactor the shell" } });
    fireEvent.click(screen.getByRole("button", { name: zhMessages.composer.send }));
    await waitUntil(() => backend.creates.length === 1, "first send to create the session");
    await settle(
      () => backend.creates[0].resolve({ session_id: "session-09" }),
      () => backend.lists.length === 2,
      "create flow to refresh catalog",
    );
    await act(async () => {
      backend.lists[1].resolve({
        contract_version: "1.1.0",
        sessions: [
          {
            session_id: "session-01",
            title: "Older session",
            provider_name: "anthropic",
            model_name: "claude-sonnet-4",
          },
        ],
      });
      await flush();
    });
    await waitUntil(
      () => controller.getState().sessionId === "session-09",
      "pending session to become selected",
    );

    const pendingRow = Array.from(
      container.querySelectorAll<HTMLButtonElement>(".session"),
    ).find((candidate) => candidate.textContent?.includes("Refactor the shell"));
    if (pendingRow === undefined) throw new Error("missing pending session row");
    fireEvent.click(pendingRow);
    expect(nav.pushes).toEqual([
      "/?session=session-09",
      "/?session=session-09",
    ]);
    expect(container.querySelector(".session.sel")?.textContent).toContain(
      "Refactor the shell",
    );

    nav.session = "session-09";
    rerenderApp();
    await act(async () => {
      await flush();
    });
    nav.session = "session-01";
    rerenderApp();
    await waitUntil(
      () => controller.getState().sessionId === "session-01",
      "browser navigation to restore URL selection",
    );
    expect(container.querySelector(".session.sel")?.textContent).toContain("Older session");
    expect(container.querySelector(".session-list")?.textContent).toContain(
      "Refactor the shell",
    );
  });

  it("surfaces create-session failures without replacing them with a catalog refresh", async () => {
    const { backend } = renderConnected();
    await resolveCatalog(backend, []);

    fireEvent.click(screen.getByRole("button", { name: zhMessages.sidebar.newSession }));
    fireEvent.change(textbox(), { target: { value: "Refactor the shell" } });
    fireEvent.click(screen.getByRole("button", { name: zhMessages.composer.send }));
    await settle(
      () => {},
      () => backend.creates.length === 1,
      "first send to create the session",
    );
    await settle(
      () =>
        backend.creates[0].reject(
          new ChatApiError(503, {
            code: "provider_unavailable",
            message: "Provider is temporarily unavailable",
            retryable: true,
          }),
        ),
      () => screen.queryByRole("alert")?.textContent?.includes("Provider is temporarily unavailable") === true,
      "create failure to reach the sidebar",
    );

    expect(screen.getByRole("alert").textContent).toContain("新建会话失败");
    expect(screen.getByRole("alert").textContent).toContain("Provider is temporarily unavailable");
    expect(backend.lists).toHaveLength(1);
    const newSessionButton = screen.getByRole("button", {
      name: zhMessages.sidebar.newSession,
    }) as HTMLButtonElement;
    expect(newSessionButton.disabled).toBe(false);
    // The draft survives the failure so the user can retry.
    expect(textbox().value).toBe("Refactor the shell");
  });

  it("creates a session on first send, renders its EventRecord turn, and accepts a follow-up", async () => {
    const { backend, container } = renderConnected();
    await resolveCatalog(backend, []);

    // The first prompt is typed into the local draft; sending it creates the
    // session, selects it, and only then starts the owning prompt stream.
    fireEvent.click(screen.getByRole("button", { name: zhMessages.sidebar.newSession }));
    fireEvent.change(textbox(), { target: { value: "Run tests" } });
    fireEvent.click(screen.getByRole("button", { name: zhMessages.composer.send }));
    await settle(
      () => {},
      () => backend.creates.length === 1,
      "first send to create the session",
    );
    await settle(
      () => backend.creates[0].resolve({ session_id: "session-09" }),
      () => backend.lists.length === 2,
      "create flow to refresh catalog",
    );
    await resolveCatalog(backend, [{ session_id: "session-09", title: null }]);
    await resolveSnapshot(backend, "session-09");
    await settle(
      () => {},
      () => backend.promptCalls.length === 1,
      "draft flow to send the first prompt",
    );
    expect(backend.promptCalls[0].request.prompt).toBe("Run tests");
    const turnEvents = [events[0], events[5], events[6]].map((event) => ({
      ...event,
      session_id: "session-09",
    }));

    await settle(
      () => {
        backend.prompts[0].push(chatItem(turnEvents[0]));
        backend.prompts[0].push(chatItem(turnEvents[1]));
        backend.prompts[0].push(chatItem(turnEvents[2]));
        backend.prompts[0].end();
      },
      () => backend.snapshots.length === 2,
      "completed owning stream to request canonical reload",
    );
    await settle(
      () =>
        backend.snapshots[1].resolve(
          makeSnapshot("session-09", turnEvents),
        ),
      () => textbox().value === "",
      "canonical completed turn to restore the composer",
    );

    expect(timelineText(container)).toContain("Run tests");
    expect(timelineText(container)).toContain("All tests pass.");
    fireEvent.change(textbox(), { target: { value: "Now lint" } });
    fireEvent.click(screen.getByRole("button", { name: zhMessages.composer.send }));
    await settle(
      () => {},
      () => backend.promptCalls.length === 2,
      "follow-up to start a second prompt",
    );
    expect(backend.promptCalls[1].request.prompt).toBe("Now lint");
  });
});

describe("AppFrame URL-led selection", () => {
  it("clicking a row pushes ?session= without selecting directly", async () => {
    const { backend, controller, container } = await renderFollowing();

    const rows = container.querySelectorAll(".session");
    fireEvent.click(rows[1]);

    // The row click only navigates; the URL remains the selection authority.
    expect(nav.pushes).toEqual(["/?session=session-02"]);
    expect(backend.snapshotCalls.map((call) => call.sessionId)).toEqual(["session-01"]);
    expect(controller.getState().sessionId).toBe("session-01");
  });

  it("a search-param change (back/forward) selects through the controller", async () => {
    const { backend, container, rerenderApp } = await renderFollowing();

    nav.session = "session-02";
    rerenderApp();
    await settle(
      () => {},
      () => backend.snapshotCalls.length === 2,
      "url change to request session-02 snapshot",
    );

    expect(backend.snapshotCalls.map((call) => call.sessionId)).toEqual([
      "session-01",
      "session-02",
    ]);
    await resolveSnapshot(backend, "session-02");

    expect(container.querySelector(".sessionbar-title")?.textContent).toBe("Fix lint");
    const selected = container.querySelector(".session.sel");
    expect(selected?.textContent).toContain("Fix lint");
    expect(railHealth(container)).toBe("ok");
  });

  it("a late snapshot from a stale selection never reaches the view", async () => {
    const { backend, container, rerenderApp } = renderConnected();
    await resolveCatalog(backend, twoSessions);
    // session-01 auto-selected; its snapshot stays PENDING.
    expect(backend.snapshotCalls.map((call) => call.sessionId)).toEqual(["session-01"]);

    // Back/forward lands before the first snapshot resolves.
    nav.session = "session-02";
    rerenderApp();
    await settle(
      () => {},
      () => backend.snapshotCalls.length === 2,
      "stale navigation to request session-02 snapshot",
    );
    expect(backend.snapshotCalls.map((call) => call.sessionId)).toEqual([
      "session-01",
      "session-02",
    ]);

    await resolveSnapshot(backend, "session-02");
    // NOW the stale session-01 snapshot resolves late, carrying events.
    await settle(
      () => backend.snapshots[0].resolve(makeSnapshot("session-01", events)),
      () => container.querySelector(".sessionbar-title")?.textContent === "Fix lint",
      "stale snapshot to leave session-02 selected",
    );

    expect(container.querySelector(".sessionbar-title")?.textContent).toBe("Fix lint");
    expect(timelineText(container)).not.toContain("Run tests");
    expect(timelineText(container)).toContain(zhMessages.timeline.empty);
    expect(railHealth(container)).toBe("ok");
  });
});

describe("AppFrame timeline and composer", () => {
  it("renders snapshot events as timeline messages and a live status readout", async () => {
    const { backend, container } = renderConnected();
    await resolveCatalog(backend, twoSessions);
    await resolveSnapshot(backend, "session-01", events);

    const text = timelineText(container);
    expect(text).toContain("Run tests");
    expect(text).toContain("Inspecting test suite");
    expect(text).toContain("bash");
    expect(text).toContain("42 passed");
    expect(text).toContain("All tests pass.");
    expect(text).toContain(zhMessages.timeline.terminalCompletedRole);
    expect(text).toContain(zhMessages.timeline.terminalInterruptedRole);

    expect(sessionbarText(container)).toContain(zhMessages.sessionbar.statusFollowing);
    expect(railHealth(container)).toBe("ok");
  });

  it("uses honest conversation labels and omits placeholder telemetry", async () => {
    const { backend, container } = renderConnected();
    await resolveCatalog(backend, twoSessions);
    await resolveSnapshot(backend, "session-01");

    expect(screen.getByRole("tab", { name: "事件" })).toBeDefined();
    expect(container.textContent).not.toContain("轨迹");
    expect(sessionbarText(container)).not.toContain("12.4s");
    expect(sessionbarText(container)).not.toContain("ctx 41%");
  });

  it("sends the composer draft and clears it on canonical admission", async () => {
    const { backend, container } = await renderFollowing();

    fireEvent.change(textbox(), { target: { value: "Run tests" } });
    fireEvent.click(screen.getByRole("button", { name: zhMessages.composer.send }));

    expect(backend.promptCalls).toHaveLength(1);
    expect(backend.promptCalls[0].request.prompt).toBe("Run tests");
    expect(backend.promptCalls[0].request.command_id.length).toBeGreaterThan(0);
    // The draft is kept until canonical admission (J3).
    expect(textbox().value).toBe("Run tests");
    // While the owning stream is active the cancel action is available.
    expect(screen.getByRole("button", { name: zhMessages.composer.cancel })).toBeDefined();

    await settle(
      () => backend.prompts[0].push(chatItem(events[0])),
      () => textbox().value === "",
      "canonical admission to clear the draft",
    );

    expect(textbox().value).toBe("");
    expect(timelineText(container)).toContain("Run tests");
  });

  it("cancel after admission surfaces the cancelling status", async () => {
    const { backend, container } = await renderFollowing();

    fireEvent.change(textbox(), { target: { value: "Run tests" } });
    fireEvent.click(screen.getByRole("button", { name: zhMessages.composer.send }));
    await settle(
      () => backend.prompts[0].push(chatItem(events[0])),
      () => textbox().value === "",
      "canonical admission before cancel",
    );
    fireEvent.click(screen.getByRole("button", { name: zhMessages.composer.cancel }));

    expect(backend.cancels).toHaveLength(1);
    await settle(
      () =>
        backend.cancels[0].resolve({
          contract_version: "1.1.0",
          session_id: "session-01",
          run_id: "run-01",
          status: "cancelling",
        }),
      () => sessionbarText(container).includes(zhMessages.sessionbar.statusCancelling),
      "cancel ack to show cancelling",
    );

    expect(sessionbarText(container)).toContain(zhMessages.sessionbar.statusCancelling);
  });

  it("offers resume from a durable interrupted terminal", async () => {
    const { backend } = await renderFollowing();

    await settle(
      () => backend.follows[0].push(chatItem(events[9])),
      () => screen.queryByRole("button", { name: zhMessages.composer.resume }) !== null,
      "interrupted terminal to offer resume",
    );

    fireEvent.click(screen.getByRole("button", { name: zhMessages.composer.resume }));
    await settle(
      () => {},
      () => backend.resumeCalls.length === 1,
      "resume click to start owning stream",
    );

    expect(backend.resumeCalls).toHaveLength(1);
    expect(backend.resumeCalls[0].request.parent_run_id).toBe("run-04");
  });

  it("surfaces replay_required with the machine-readable reason and reload action", async () => {
    const { backend, container } = await renderFollowing();

    await settle(
      () =>
        backend.follows[0].push({
          type: "stream_control",
          control: {
            contract_version: "1.1.0",
            kind: "replay_required",
            reason: "sequence_loss",
            cursor: "cursor-2",
          },
        }),
      () => timelineText(container).includes(zhMessages.timeline.replayRequired),
      "replay_required control to reach the timeline",
    );

    const text = timelineText(container);
    expect(text).toContain(zhMessages.timeline.replayRequired);
    expect(text).toContain("sequence_loss");
    expect(sessionbarText(container)).toContain(zhMessages.sessionbar.statusReplayRequired);
    expect(railHealth(container)).toBe("down");

    const snapshotsBefore = backend.snapshots.length;
    fireEvent.click(screen.getByRole("button", { name: zhMessages.composer.reload }));
    await settle(
      () => {},
      () => backend.snapshots.length === snapshotsBefore + 1,
      "reload to request a canonical snapshot",
    );
    // Reload is a canonical reload of the selected session.
    expect(backend.snapshots.length).toBe(snapshotsBefore + 1);
  });

  it("disables Send and hides Resume during replay_required so no owning stream starts", async () => {
    const { backend } = await renderFollowing();

    await settle(
      () => backend.follows[0].push(chatItem(events[9])),
      () => screen.queryByRole("button", { name: zhMessages.composer.resume }) !== null,
      "interrupted terminal to offer resume",
    );
    expect(screen.getByRole("button", { name: zhMessages.composer.resume })).toBeDefined();

    await settle(
      () =>
        backend.follows[0].push({
          type: "stream_control",
          control: {
            contract_version: "1.1.0",
            kind: "replay_required",
            reason: "sequence_loss",
            cursor: "cursor-2",
          },
        }),
      () => (screen.getByRole("button", { name: zhMessages.composer.send }) as HTMLButtonElement).disabled,
      "replay_required to disable Send",
    );

    fireEvent.change(textbox(), { target: { value: "retry this turn" } });
    const send = screen.getByRole("button", { name: zhMessages.composer.send }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    expect(screen.queryByRole("button", { name: zhMessages.composer.resume })).toBeNull();
    expect(screen.queryByRole("button", { name: zhMessages.composer.cancel })).toBeNull();
    fireEvent.click(send);
    fireEvent.keyDown(textbox(), { key: "Enter" });
    expect(backend.promptCalls).toHaveLength(0);
    expect(backend.resumeCalls).toHaveLength(0);
  });

  it("reload after replay_required restores follow and re-enables Send", async () => {
    const { backend } = await renderFollowing();

    await settle(
      () =>
        backend.follows[0].push({
          type: "stream_control",
          control: {
            contract_version: "1.1.0",
            kind: "replay_required",
            reason: "sequence_loss",
            cursor: "cursor-2",
          },
        }),
      () => screen.queryByRole("button", { name: zhMessages.composer.reload }) !== null,
      "replay_required to offer reload",
    );

    fireEvent.click(screen.getByRole("button", { name: zhMessages.composer.reload }));
    await resolveSnapshot(backend, "session-01", [events[0]]);
    fireEvent.change(textbox(), { target: { value: "after reload" } });

    const send = screen.getByRole("button", { name: zhMessages.composer.send }) as HTMLButtonElement;
    expect(send.disabled).toBe(false);
    expect(backend.followCalls).toHaveLength(2);
    fireEvent.click(send);
    await settle(
      () => {},
      () => backend.promptCalls.length === 1,
      "send after reload to start prompt",
    );
    expect(backend.promptCalls).toHaveLength(1);
    expect(backend.promptCalls[0].request.prompt).toBe("after reload");
  });

  it("surfaces a snapshot failure as a timeline error with down health", async () => {
    const { backend, container } = renderConnected();
    await resolveCatalog(backend, twoSessions);
    await settle(
      () => backend.snapshots[0].reject(new Error("snapshot boom")),
      () => railHealth(container) === "down",
      "snapshot failure to mark rail down",
    );

    // Both the timeline and the composer surface the error; assert the
    // timeline alert inside its own region.
    const region = container.querySelector(".timeline-scroll");
    if (region === null) throw new Error("missing .timeline-scroll region");
    const alerts = Array.from(region.querySelectorAll("[role='alert']"));
    expect(alerts).toHaveLength(1);
    expect(alerts[0].textContent).toContain(zhMessages.timeline.error);
    expect(alerts[0].textContent).toContain("snapshot boom");
    expect(sessionbarText(container)).toContain(zhMessages.sessionbar.statusError);
    expect(railHealth(container)).toBe("down");
  });

  it("surfaces a follow failure as reconnecting with degraded health", async () => {
    const { backend, container } = await renderFollowing();

    await settle(
      () => backend.follows[0].fail(new Error("network down")),
      () => railHealth(container) === "degraded",
      "follow failure to mark rail degraded",
    );

    expect(timelineText(container)).toContain(zhMessages.timeline.reconnecting);
    expect(sessionbarText(container)).toContain(zhMessages.sessionbar.statusReconnecting);
    expect(railHealth(container)).toBe("degraded");
  });

  it("treats fixture cursor_expired as replay_required with Reload and no extra follow", async () => {
    const { backend, container } = await renderFollowing();
    const expired = fixture.cursor.errors.find((entry) => entry.case === "expired");
    if (!expired) throw new Error("fixture missing expired cursor case");

    await settle(
      () =>
        backend.follows[0].fail(
          new ChatApiError(expired.status, {
            code: expired.reason,
            message: "cursor expired",
            retryable: false,
            replay_required: expired.replay_required,
          }),
        ),
      () => screen.queryByRole("button", { name: zhMessages.composer.reload }) !== null,
      "cursor_expired to offer reload",
    );

    expect(timelineText(container)).toContain(zhMessages.timeline.replayRequired);
    expect(timelineText(container)).toContain("cursor_expired");
    expect(sessionbarText(container)).toContain(zhMessages.sessionbar.statusReplayRequired);
    expect(railHealth(container)).toBe("down");
    expect(screen.getByRole("button", { name: zhMessages.composer.reload })).toBeDefined();
    expect((screen.getByRole("button", { name: zhMessages.composer.send }) as HTMLButtonElement).disabled).toBe(true);
    expect(backend.followCalls).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: zhMessages.composer.reload }));
    await resolveSnapshot(backend, "session-01", [events[0]]);
    fireEvent.change(textbox(), { target: { value: "after expired cursor" } });
    expect((screen.getByRole("button", { name: zhMessages.composer.send }) as HTMLButtonElement).disabled).toBe(false);
    expect(backend.followCalls).toHaveLength(2);
  });

  it("treats fixture credentials_required 401 as a stable error and does not reconnect", async () => {
    const { backend, container } = await renderFollowing();
    const auth = fixture.http.errors.auth[0];
    if (!auth) throw new Error("fixture missing credentials_required auth case");

    await settle(
      () => backend.follows[0].fail(new ChatApiError(auth.status, auth.body.error)),
      () => railHealth(container) === "down",
      "credentials_required to mark rail down",
    );

    const region = container.querySelector(".timeline-scroll");
    if (region === null) throw new Error("missing .timeline-scroll region");
    const alerts = Array.from(region.querySelectorAll("[role='alert']"));
    expect(alerts).toHaveLength(1);
    expect(alerts[0].textContent).toContain(zhMessages.timeline.error);
    expect(alerts[0].textContent).toContain("credentials_required");
    expect(sessionbarText(container)).toContain(zhMessages.sessionbar.statusError);
    expect(railHealth(container)).toBe("down");
    expect(screen.queryByRole("button", { name: zhMessages.composer.reload })).toBeNull();
    expect(timelineText(container)).not.toContain(zhMessages.timeline.reconnecting);
    expect(backend.followCalls).toHaveLength(1);
  });
});
