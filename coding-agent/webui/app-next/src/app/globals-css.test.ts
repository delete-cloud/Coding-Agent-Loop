/**
 * Source assertions for globals.css (01 §1.4 / 03 rule 3).
 *
 * Amber is a signal color with exactly four legal sites: rail active, the
 * selected sidebar session row, focus borders, and the `interrupted` status
 * word (plus the token definitions and their shadcn aliases inside :root).
 * The details toggle active state must be neutral — this test pins both the
 * toggle rule and the global amber allowlist so a fifth amber site cannot
 * sneak back in.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const cssPath = join(dirname(fileURLToPath(import.meta.url)), "globals.css");
const css = readFileSync(cssPath, "utf8");

/** Parse top-level and media-nested rules as { selector, body } pairs. */
function parseRules(source: string): Array<{ selector: string; body: string }> {
  const stripped = source
    .replace(/\/\*[\s\S]*?\*\//g, "") // comments
    .replace(/@import[^;]*;/g, ""); // @import statements are not rules
  const rules: Array<{ selector: string; body: string }> = [];
  for (const match of stripped.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    rules.push({ selector: match[1].trim(), body: match[2] });
  }
  return rules;
}

const rules = parseRules(css);

// The four legal amber sites outside :root (01 §1.4 / 03 rule 3).
const AMBER_SITE_ALLOWLIST = new Set([
  ":root", // token definitions + shadcn aliases (--primary, --ring)
  ".rail-btn.active",
  ".session.sel",
  ".search:focus",
  ".readout em",
]);

describe("globals.css amber discipline (01 §1.4)", () => {
  it("details toggle active state is neutral: --fg text, --hairline-2 border, no amber", () => {
    const toggle = rules.find((r) => r.selector === '.details-toggle[aria-expanded="true"]');
    expect(toggle).toBeDefined();
    expect(toggle?.body).toContain("var(--fg)");
    expect(toggle?.body).toContain("var(--hairline-2)");
    expect(toggle?.body).not.toContain("amber");
  });

  it("amber appears only at the four legal sites plus the :root token block", () => {
    const amberRules = rules.filter((r) => r.body.includes("var(--amber"));
    const selectors = amberRules.map((r) => r.selector);

    for (const selector of selectors) {
      expect(
        AMBER_SITE_ALLOWLIST.has(selector),
        `unexpected amber site: ${selector}`,
      ).toBe(true);
    }
    // All four legal sites are actually present (guard against an over-broad
    // allowlist passing with zero matches).
    expect(selectors).toContain(".rail-btn.active");
    expect(selectors).toContain(".session.sel");
    expect(selectors).toContain(".search:focus");
    expect(selectors).toContain(".readout em");
  });

  it("raw hex / rgba values appear only inside :root (01 §6 / 04 §3)", () => {
    const hardCoded = rules.filter(
      (r) => /#[0-9a-fA-F]{3,8}\b|rgba?\(/.test(r.body) && r.selector !== ":root",
    );
    expect(hardCoded).toEqual([]);
  });
});
