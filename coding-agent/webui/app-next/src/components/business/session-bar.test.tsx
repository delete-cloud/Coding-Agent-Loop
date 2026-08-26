import { createRef } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";

import enMessages from "../../../messages/en.json";
import zhMessages from "../../../messages/zh.json";
import type { ConnectedChatStatus } from "@/lib/connected-chat/controller";
import { SessionBar } from "@/components/business/session-bar";

type SessionBarProps = Parameters<typeof SessionBar>[0];

function renderSessionBar(
  props: Partial<SessionBarProps> = {},
  locale: "zh" | "en" = "zh",
) {
  const toggleRef = createRef<HTMLButtonElement>();
  const base: SessionBarProps = {
    title: "refactor: three-pane shell",
    detailsOpen: false,
    onToggleDetails: vi.fn(),
    toggleRef,
    ...props,
  };
  const result = render(
    <NextIntlClientProvider
      locale={locale}
      messages={locale === "zh" ? zhMessages : enMessages}
    >
      <SessionBar {...base} />
    </NextIntlClientProvider>,
  );
  return { ...result, props: base, toggleRef };
}

describe("SessionBar static shell (no chatStatus prop)", () => {
  it("keeps the placeholder readout and title, and shows unset provider/model", () => {
    const { container } = renderSessionBar();

    expect(container.querySelector(".sessionbar-title")?.textContent).toBe(
      "refactor: three-pane shell",
    );
    expect(container.querySelector(".sessionbar-prov")?.textContent).toBe(
      zhMessages.sessionbar.providerModelUnset,
    );
    const readout = container.querySelector(".readout");
    expect(readout?.textContent).toBe(zhMessages.sessionbar.readoutStatus);
  });
});

describe("SessionBar live provider · model", () => {
  it("renders the live provider · model line instead of an i18n placeholder", () => {
    const { container } = renderSessionBar({ providerModel: "deepseek · deepseek-chat" });
    expect(container.querySelector(".sessionbar-prov")?.textContent).toBe(
      "deepseek · deepseek-chat",
    );
  });
});

describe("SessionBar Settings and Theme buttons", () => {
  it("wires Settings and Theme clicks", () => {
    const onOpenSettings = vi.fn();
    const onToggleTheme = vi.fn();
    renderSessionBar({ onOpenSettings, onToggleTheme, theme: "dark" });

    fireEvent.click(screen.getByTitle(zhMessages.sessionbar.settings));
    expect(onOpenSettings).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: zhMessages.sessionbar.themeToLight }));
    expect(onToggleTheme).toHaveBeenCalledTimes(1);
  });
});

describe("SessionBar details toggle", () => {
  it("forwards the ref, reflects detailsOpen via aria-expanded, and calls onToggleDetails", () => {
    const onToggleDetails = vi.fn();
    const { container, toggleRef, unmount } = renderSessionBar({
      detailsOpen: false,
      onToggleDetails,
    });

    const toggle = container.querySelector<HTMLButtonElement>("button.details-toggle");
    expect(toggle).not.toBeNull();
    expect(toggleRef.current).toBe(toggle);
    expect(toggle?.getAttribute("aria-expanded")).toBe("false");
    expect(toggle?.getAttribute("aria-controls")).toBe("details-pane");
    expect(toggle?.getAttribute("title")).toBe(zhMessages.sessionbar.detailsToggle);

    fireEvent.click(toggle!);
    expect(onToggleDetails).toHaveBeenCalledTimes(1);
    unmount();

    const open = renderSessionBar({ detailsOpen: true });
    expect(
      open.container
        .querySelector("button.details-toggle")
        ?.getAttribute("aria-expanded"),
    ).toBe("true");
  });
});

describe("SessionBar connected-chat status label", () => {
  const cases: Array<{ status: ConnectedChatStatus; zh: string; en: string }> = [
    { status: "idle", zh: zhMessages.sessionbar.statusIdle, en: enMessages.sessionbar.statusIdle },
    { status: "loading", zh: zhMessages.sessionbar.statusLoading, en: enMessages.sessionbar.statusLoading },
    { status: "following", zh: zhMessages.sessionbar.statusFollowing, en: enMessages.sessionbar.statusFollowing },
    { status: "sending", zh: zhMessages.sessionbar.statusSending, en: enMessages.sessionbar.statusSending },
    { status: "cancelling", zh: zhMessages.sessionbar.statusCancelling, en: enMessages.sessionbar.statusCancelling },
    { status: "reconnecting", zh: zhMessages.sessionbar.statusReconnecting, en: enMessages.sessionbar.statusReconnecting },
    { status: "replay_required", zh: zhMessages.sessionbar.statusReplayRequired, en: enMessages.sessionbar.statusReplayRequired },
    { status: "error", zh: zhMessages.sessionbar.statusError, en: enMessages.sessionbar.statusError },
  ];

  for (const { status, zh } of cases) {
    it(`renders the ${status} label in the readout status slot (zh)`, () => {
      const { container } = renderSessionBar({ chatStatus: status });

      const statusWord = container.querySelector(".readout span");
      expect(statusWord?.textContent).toBe(zh);
      expect(container.querySelector(".readout em")).toBeNull();
      expect(container.querySelector(".readout")?.textContent).toBe(zh);
    });
  }

  it("renders the status label in the en locale", () => {
    const { container } = renderSessionBar({ chatStatus: "error" }, "en");

    expect(container.querySelector(".readout span")?.textContent).toBe(
      enMessages.sessionbar.statusError,
    );
    expect(container.querySelector(".readout em")).toBeNull();
  });
});

describe("SessionBar amber discipline (01 §1.4 / 03 rule 3)", () => {
  it("keeps the static interrupted placeholder in the amber readout em", () => {
    const { container } = renderSessionBar();

    expect(container.querySelector(".readout em")?.textContent).toBe(
      zhMessages.sessionbar.readoutStatus,
    );
  });

  it("keeps connected transport status words out of the amber readout em", () => {
    const statuses: ConnectedChatStatus[] = [
      "idle",
      "loading",
      "following",
      "sending",
      "cancelling",
      "reconnecting",
      "replay_required",
      "error",
    ];

    for (const status of statuses) {
      const { container, unmount } = renderSessionBar({ chatStatus: status });
      expect(
        container.querySelector(".readout em"),
        `${status} must not inherit the interrupted amber slot`,
      ).toBeNull();
      expect(container.querySelector(".readout")?.textContent).toContain(
        zhMessages.sessionbar[
          (
            {
              idle: "statusIdle",
              loading: "statusLoading",
              following: "statusFollowing",
              sending: "statusSending",
              cancelling: "statusCancelling",
              reconnecting: "statusReconnecting",
              replay_required: "statusReplayRequired",
              error: "statusError",
            } as const
          )[status]
        ],
      );
      unmount();
    }
  });
});
