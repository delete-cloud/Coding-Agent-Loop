import { describe, expect, it } from "vitest";

import {
  formatProviderAccountLabel,
  listableProviders,
  resolveProviderAccount,
} from "@/lib/session-settings";

const labeled = "codex:kina0630test-gmail-com";

describe("listableProviders", () => {
  it("lists Codex once and keeps OAuth accounts out of the provider list", () => {
    expect(listableProviders()).toContain("codex");
    expect(listableProviders()).not.toContain(labeled);
    expect(listableProviders().filter((item) => item === "codex")).toEqual(["codex"]);
  });
});

describe("resolveProviderAccount", () => {
  it("maps bare codex to the connected labeled account", () => {
    expect(resolveProviderAccount("codex", [labeled])).toBe(labeled);
    expect(resolveProviderAccount("codex", ["codex"])).toBe("codex");
    expect(resolveProviderAccount("anthropic", [labeled])).toBe("anthropic");
  });
});

describe("formatProviderAccountLabel", () => {
  it("shows Codex accounts by label instead of the raw provider id", () => {
    expect(
      formatProviderAccountLabel(labeled, [{ provider: labeled, label: "kina" }]),
    ).toBe("Codex · kina");
    expect(
      formatProviderAccountLabel(labeled, [{ provider: labeled, label: "kina" }]),
    ).not.toContain("kina0630test-gmail-com");
  });

  it("falls back to Codex when no account label is available", () => {
    expect(formatProviderAccountLabel("codex", [])).toBe("Codex");
    expect(formatProviderAccountLabel(labeled, [])).toBe("Codex");
  });
});


