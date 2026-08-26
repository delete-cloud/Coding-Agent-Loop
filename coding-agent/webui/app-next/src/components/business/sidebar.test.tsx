import { fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";

import zhMessages from "../../../messages/zh.json";
import { Sidebar, type SessionItem, type SidebarProps } from "@/components/business/sidebar";

const sessions: SessionItem[] = [
  { id: "s1", title: "refactor: three-pane shell", meta: "k3 · 2h ago · 48 msgs" },
  { id: "s2", title: "fix: SSE reconnect jitter", meta: "main · 5h ago · 21 msgs" },
];

// Distributive override type: makes the shared base props optional while
// keeping the discriminated union intact — the `status`/`onRetry` pairing
// (incl. the required pair on the "error" variant) is preserved exactly as
// production declares it, so overrides are type-checked per variant.
type SidebarOverrides<T> = T extends unknown
  ? Partial<Omit<T, "status" | "onRetry">> & Pick<T, Extract<keyof T, "status" | "onRetry">>
  : never;

function renderSidebar(props: SidebarOverrides<SidebarProps> = {}) {
  return render(
    <NextIntlClientProvider locale="zh" messages={zhMessages}>
      <Sidebar
        sessions={sessions}
        selectedId="s1"
        onSelect={() => {}}
        {...props}
      />
    </NextIntlClientProvider>,
  );
}

describe("Sidebar session rows", () => {
  it("renders every session row with title, meta, and the selected row marked", () => {
    const { container } = renderSidebar();

    const rows = container.querySelectorAll(".session");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("refactor: three-pane shell");
    expect(rows[0].textContent).toContain("k3 · 2h ago · 48 msgs");
    expect(rows[1].textContent).toContain("fix: SSE reconnect jitter");

    const selected = container.querySelector(".session.sel");
    expect(selected?.textContent).toContain("refactor: three-pane shell");
    expect(selected?.getAttribute("aria-current")).toBe("page");
    expect(rows[1].getAttribute("aria-current")).toBeNull();
  });

  it("calls onSelect with the clicked session id", () => {
    const onSelect = vi.fn();
    const { container } = renderSidebar({ onSelect });

    const rows = container.querySelectorAll(".session");
    fireEvent.click(rows[1]);

    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith("s2");
  });
});

describe("Sidebar catalog states", () => {
  it("shows the loading note and no session rows while loading", () => {
    const { container } = renderSidebar({ status: "loading" });

    expect(screen.getByText(zhMessages.sidebar.loading)).toBeDefined();
    expect(container.querySelectorAll(".session")).toHaveLength(0);
  });

  it("shows the empty note when the catalog is ready with zero sessions", () => {
    const { container } = renderSidebar({ sessions: [], selectedId: "" });

    expect(screen.getByText(zhMessages.sidebar.empty)).toBeDefined();
    expect(container.querySelectorAll(".session")).toHaveLength(0);
  });

  it("shows an error note with a retry action and keeps stale rows visible", () => {
    const onRetry = vi.fn();
    const { container } = renderSidebar({ status: "error", onRetry });

    expect(screen.getByText(zhMessages.sidebar.error)).toBeDefined();
    // Stale-but-valid sessions stay visible under the error note.
    expect(container.querySelectorAll(".session")).toHaveLength(2);

    fireEvent.click(screen.getByText(zhMessages.sidebar.retry));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});

describe("Sidebar new-session action", () => {
  it("calls onCreateSession when the new-session button is clicked", () => {
    const onCreateSession = vi.fn();
    renderSidebar({ onCreateSession });

    fireEvent.click(screen.getByText(zhMessages.sidebar.newSession));

    expect(onCreateSession).toHaveBeenCalledTimes(1);
  });

  it("disables the button and switches its label while creation is pending", () => {
    const onCreateSession = vi.fn();
    renderSidebar({ onCreateSession, createPending: true });

    const button = screen.getByText(zhMessages.sidebar.creating).closest("button");
    expect(button).not.toBeNull();
    expect(button?.disabled).toBe(true);
    expect(screen.queryByText(zhMessages.sidebar.newSession)).toBeNull();

    if (button) fireEvent.click(button);
    expect(onCreateSession).not.toHaveBeenCalled();
  });
});
