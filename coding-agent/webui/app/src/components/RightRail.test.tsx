// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import RightRail from "./RightRail";

const store = new Map<string, string>();

beforeEach(() => {
  store.clear();
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("RightRail label expand/collapse", () => {
  it("shows text labels after expanding and persists the choice", () => {
    render(
      <RightRail panel={null} onToggle={() => {}}>
        <div />
      </RightRail>,
    );

    // Collapsed by default: no visible label text.
    expect(screen.queryByText("diff")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Expand panel labels" }));
    expect(screen.getByText("diff")).toBeTruthy();
    expect(screen.getByText("checkpoints")).toBeTruthy();
    expect(localStorage.getItem("coding-agent-webui-rail-labels")).toBe("1");

    fireEvent.click(screen.getByRole("button", { name: "Collapse panel labels" }));
    expect(screen.queryByText("diff")).toBeNull();
    expect(localStorage.getItem("coding-agent-webui-rail-labels")).toBe("0");
  });

  it("restores the expanded state from localStorage", () => {
    localStorage.setItem("coding-agent-webui-rail-labels", "1");
    render(
      <RightRail panel={null} onToggle={() => {}}>
        <div />
      </RightRail>,
    );
    expect(screen.getByText("settings")).toBeTruthy();
  });
});
