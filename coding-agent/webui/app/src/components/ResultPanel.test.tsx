// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import ResultPanel, { hasResultContent } from "./ResultPanel";
import type { SessionResult } from "../lib/types";

afterEach(cleanup);

const result = (overrides: Partial<SessionResult> = {}): SessionResult => ({
  session_id: "s1",
  status: "completed",
  turn_status: "idle",
  provider_name: "kimi-code",
  model_name: "kimi-for-coding",
  final_answer: "All **58 tests** pass.",
  verification_summary: "vitest + tsc green",
  failure_details: null,
  ...overrides,
});

describe("ResultPanel", () => {
  it("renders the final answer as markdown with verification summary", () => {
    render(<ResultPanel result={result()} />);

    expect(screen.getByText("Session result")).toBeTruthy();
    // Markdown: bold segments render as <strong>.
    expect(screen.getByText("58 tests").tagName).toBe("STRONG");
    expect(screen.getByText(/vitest \+ tsc green/)).toBeTruthy();
    expect(screen.getByText("kimi-code / kimi-for-coding")).toBeTruthy();
  });

  it("renders failure details with error styling", () => {
    render(
      <ResultPanel
        result={result({ status: "failed", failure_details: "step 12: bash exited 1" })}
      />,
    );

    const failure = screen.getByText("step 12: bash exited 1");
    expect(failure.className).toContain("text-err");
  });

  it("bounds and scrolls the expanded details below the header", () => {
    render(<ResultPanel result={result()} />);

    const toggle = screen.getByRole("button", { name: /session result/i });
    const details = screen.getByRole("region", { name: "Session result details" });

    expect(details.className).toContain("max-h-[40vh]");
    expect(details.className).toContain("overflow-y-auto");
    expect(details.tabIndex).toBe(0);
    expect(details.contains(toggle)).toBe(false);
  });

  it("collapses and expands via the header", () => {
    render(<ResultPanel result={result()} />);

    const toggle = screen.getByRole("button", { name: /session result/i });
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(toggle);

    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText(/58 tests/)).toBeNull();

    fireEvent.click(toggle);
    expect(screen.getByText("58 tests")).toBeTruthy();
  });
});

describe("hasResultContent", () => {
  it("is false when every content field is empty", () => {
    expect(
      hasResultContent(
        result({ final_answer: null, verification_summary: null, failure_details: null }),
      ),
    ).toBe(false);
    expect(
      hasResultContent(result({ final_answer: "  ", verification_summary: "", failure_details: null })),
    ).toBe(false);
  });

  it("is true when any content field is present", () => {
    expect(
      hasResultContent(result({ final_answer: null, failure_details: "boom" })),
    ).toBe(true);
  });
});
