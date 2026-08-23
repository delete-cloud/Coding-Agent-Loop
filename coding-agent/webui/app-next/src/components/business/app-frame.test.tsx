import { fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, describe, expect, it, vi } from "vitest";

import zhMessages from "../../../messages/zh.json";
import { AppFrame } from "@/components/business/app-frame";

// AppFrame reads ?session= via next/navigation; jsdom has no Next router, so
// the navigation hooks are mocked. `nav` is mutable on purpose: tests for the
// details lifecycle change the RAW session param and rerender (02 §4.2).
const nav = vi.hoisted(() => ({ session: "s1" as string | null }));

vi.mock("next/navigation", () => ({
  useSearchParams: () =>
    new URLSearchParams(nav.session === null ? "" : `session=${nav.session}`),
  usePathname: () => "/",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}));

function appElement() {
  return (
    <NextIntlClientProvider locale="zh" messages={zhMessages}>
      <AppFrame />
    </NextIntlClientProvider>
  );
}

function renderApp() {
  return render(appElement());
}

afterEach(() => {
  nav.session = "s1";
});

describe("AppFrame shell (02 §2/§4)", () => {
  it("renders the four regions: rail, sidebar, conversation, details", () => {
    const { container } = renderApp();

    const appframe = container.querySelector(".appframe");
    expect(appframe).not.toBeNull();

    const rail = container.querySelector(".rail");
    const sidebar = container.querySelector(".sidebar");
    const conversation = container.querySelector(".conversation");
    const details = container.querySelector(".details");

    expect(rail?.tagName).toBe("NAV");
    expect(sidebar?.tagName).toBe("ASIDE");
    expect(conversation?.tagName).toBe("MAIN");
    expect(details?.tagName).toBe("ASIDE");

    // SessionBar is a single header row inside the conversation column.
    expect(conversation?.querySelector("header.sessionbar")).not.toBeNull();
  });

  it("has exactly three vertical scroll regions (02 §2)", () => {
    const { container } = renderApp();

    expect(container.querySelector(".session-list")).not.toBeNull();
    expect(container.querySelector(".timeline-scroll")).not.toBeNull();
    expect(container.querySelector(".details-scroll")).not.toBeNull();
    // The details pane has exactly one scroll container (no per-section scroll).
    expect(container.querySelectorAll(".details .details-scroll")).toHaveLength(1);
  });

  it("details pane is closed by default and stays mounted but inert (02 §4)", () => {
    const { container } = renderApp();

    const details = container.querySelector(".details");
    expect(details).not.toBeNull();
    expect(details?.classList.contains("closed")).toBe(true);
    expect(details?.getAttribute("aria-hidden")).toBe("true");
    expect(details?.hasAttribute("inert")).toBe(true);
    // Collapsed, NOT unmounted: the scroll subtree is still in the DOM.
    expect(details?.querySelector(".details-scroll")).not.toBeNull();

    const toggle = screen.getByTitle(zhMessages.sessionbar.detailsToggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(toggle.getAttribute("aria-controls")).toBe("details-pane");
    expect(details?.id).toBe("details-pane");
  });

  it("opens the details pane via the SessionBar toggle", () => {
    const { container } = renderApp();

    fireEvent.click(screen.getByTitle(zhMessages.sessionbar.detailsToggle));

    const details = container.querySelector(".details");
    expect(details?.classList.contains("closed")).toBe(false);
    expect(details?.getAttribute("aria-hidden")).toBe("false");
    expect(details?.hasAttribute("inert")).toBe(false);
    expect(
      screen.getByTitle(zhMessages.sessionbar.detailsToggle).getAttribute("aria-expanded"),
    ).toBe("true");
  });

  it("renders sidebar session rows with the ?session= row selected", () => {
    const { container } = renderApp();

    const rows = container.querySelectorAll(".session");
    expect(rows).toHaveLength(4);
    const selected = container.querySelector(".session.sel");
    expect(selected?.textContent).toContain(zhMessages.sidebar.sessions.s1.title);
    expect(selected?.getAttribute("aria-current")).toBe("page");
  });

  it("closes details when the raw ?session= param becomes absent (02 §4.2)", () => {
    const { container, rerender } = renderApp();

    fireEvent.click(screen.getByTitle(zhMessages.sessionbar.detailsToggle));
    expect(container.querySelector(".details")?.classList.contains("closed")).toBe(false);

    // s1 → absent normalizes back to selectedId="s1"; the close effect must
    // still fire because the RAW query parameter changed.
    nav.session = null;
    rerender(appElement());

    const details = container.querySelector(".details");
    expect(details?.classList.contains("closed")).toBe(true);
    expect(details?.getAttribute("aria-hidden")).toBe("true");
    expect(
      screen.getByTitle(zhMessages.sessionbar.detailsToggle).getAttribute("aria-expanded"),
    ).toBe("false");
  });

  it("closes details when the raw ?session= param becomes invalid (02 §4.2)", () => {
    const { container, rerender } = renderApp();

    fireEvent.click(screen.getByTitle(zhMessages.sessionbar.detailsToggle));
    expect(container.querySelector(".details")?.classList.contains("closed")).toBe(false);

    // s1 → invalid also normalizes back to selectedId="s1"; the raw param
    // change alone must close the pane.
    nav.session = "no-such-session";
    rerender(appElement());

    const details = container.querySelector(".details");
    expect(details?.classList.contains("closed")).toBe(true);
    expect(details?.getAttribute("aria-hidden")).toBe("true");
    expect(
      screen.getByTitle(zhMessages.sessionbar.detailsToggle).getAttribute("aria-expanded"),
    ).toBe("false");
  });
});
