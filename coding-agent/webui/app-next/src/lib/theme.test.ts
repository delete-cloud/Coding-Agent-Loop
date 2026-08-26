import { afterEach, describe, expect, it } from "vitest";

import { applyTheme, readStoredTheme, THEME_LS_KEY, toggleTheme } from "./theme";

afterEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

describe("theme toggle persistence", () => {
  it("defaults to dark when nothing is stored", () => {
    expect(readStoredTheme()).toBe("dark");
  });

  it("applies html[data-theme] and persists the choice", () => {
    applyTheme("light");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(localStorage.getItem(THEME_LS_KEY)).toBe("light");
    expect(readStoredTheme()).toBe("light");
  });

  it("toggles dark to light and back", () => {
    expect(toggleTheme("dark")).toBe("light");
    applyTheme(toggleTheme("dark"));
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    applyTheme(toggleTheme("light"));
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(localStorage.getItem(THEME_LS_KEY)).toBe("dark");
  });
});
