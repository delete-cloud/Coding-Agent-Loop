import { render } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it } from "vitest";

import zhMessages from "../../../messages/zh.json";
import { Rail } from "@/components/business/rail";
import type { RailHealth } from "@/hooks/use-connected-chat";

function renderRail(health?: RailHealth) {
  return render(
    <NextIntlClientProvider locale="zh" messages={zhMessages}>
      <Rail health={health} />
    </NextIntlClientProvider>,
  );
}

describe("Rail", () => {
  it("renders the static shell unchanged when no health is wired", () => {
    const { container } = renderRail();

    expect(container.querySelectorAll(".rail-btn")).toHaveLength(2);
    const dot = container.querySelector(".rail-dot");
    expect(dot).not.toBeNull();
    expect(dot?.getAttribute("role")).toBe("img");
    expect(dot?.getAttribute("aria-label")).toBe(zhMessages.rail.health);
    // No health prop → neutral static placeholder, no health attribute.
    expect(dot?.getAttribute("data-health")).toBeNull();
  });

  it("reflects a typed transport health on the rail dot", () => {
    const cases: RailHealth[] = ["idle", "ok", "degraded", "down"];
    for (const health of cases) {
      const { container, unmount } = renderRail(health);
      const dot = container.querySelector(".rail-dot");
      expect(dot?.getAttribute("data-health")).toBe(health);
      expect(dot?.getAttribute("aria-label")).toBe(zhMessages.rail.health);
      unmount();
    }
  });
});
