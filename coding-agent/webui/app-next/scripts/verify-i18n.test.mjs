/**
 * Fixture-driven tests for scripts/verify-i18n.mjs.
 *
 * Each test builds a minimal fake app root (src/ + messages/) in a temp dir
 * and runs the verifier against it via `node scripts/verify-i18n.mjs <root>`.
 * Exit codes and stderr are asserted for real — no truthiness shortcuts.
 */
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterAll, describe, expect, it } from "vitest";

const script = join(dirname(fileURLToPath(import.meta.url)), "verify-i18n.mjs");

const tempRoots = [];

function makeFixture({ code, zh, en }) {
  const root = mkdtempSync(join(tmpdir(), "verify-i18n-fixture-"));
  tempRoots.push(root);
  mkdirSync(join(root, "src"), { recursive: true });
  mkdirSync(join(root, "messages"), { recursive: true });
  writeFileSync(join(root, "src", "comp.tsx"), code);
  writeFileSync(join(root, "messages", "zh.json"), JSON.stringify(zh));
  writeFileSync(join(root, "messages", "en.json"), JSON.stringify(en));
  return root;
}

function runVerifier(root) {
  return spawnSync(process.execPath, [script, root], { encoding: "utf8" });
}

afterAll(() => {
  for (const root of tempRoots) {
    rmSync(root, { recursive: true, force: true });
  }
});

const ALIAS_CODE = [
  'import { useTranslations } from "next-intl";',
  'const tSessions = useTranslations("sidebar.sessions");',
  'const t = useTranslations("sessionbar");',
  'export const a = tSessions("s1.title");',
  'export const b = tSessions("s2.meta");',
  'export const c = t("detailsToggle");',
].join("\n");

const FULL_MESSAGES = {
  sidebar: { sessions: { s1: { title: "标题一" }, s2: { meta: "meta" } } },
  sessionbar: { detailsToggle: "详情" },
};

describe("verify-i18n.mjs alias binding discovery", () => {
  it("(a) captures keys referenced through useTranslations aliases", () => {
    const root = makeFixture({ code: ALIAS_CODE, zh: FULL_MESSAGES, en: FULL_MESSAGES });

    const result = runVerifier(root);

    expect(result.status).toBe(0);
    // Exactly the 3 alias-referenced keys were collected — a verifier that
    // missed the alias binding would report 0 or 1 referenced key(s).
    expect(result.stdout).toContain("3 referenced key(s)");
  });

  it("(b) exits nonzero when one alias-referenced key is removed from zh", () => {
    const zhMissingAliasKey = {
      sidebar: { sessions: { s2: { meta: "meta" } } }, // s1.title removed
      sessionbar: { detailsToggle: "详情" },
    };
    const root = makeFixture({ code: ALIAS_CODE, zh: zhMissingAliasKey, en: FULL_MESSAGES });

    const result = runVerifier(root);

    expect(result.status).not.toBe(0);
    expect(result.stderr).toContain("sidebar.sessions.s1.title");
    expect(result.stderr).toContain("messages/zh.json");
  });

  it("(b) exits nonzero when one alias-referenced key is removed from en", () => {
    const enMissingAliasKey = {
      sidebar: { sessions: { s1: { title: "Title 1" }, s2: { meta: "meta" } } },
      sessionbar: {}, // detailsToggle removed
    };
    const root = makeFixture({ code: ALIAS_CODE, zh: FULL_MESSAGES, en: enMissingAliasKey });

    const result = runVerifier(root);

    expect(result.status).not.toBe(0);
    expect(result.stderr).toContain("sessionbar.detailsToggle");
    expect(result.stderr).toContain("messages/en.json");
  });

  it("(c) rejects computed/template-literal keys even when the literal keys exist", () => {
    const computedCode = [
      'import { useTranslations } from "next-intl";',
      'const tSessions = useTranslations("sidebar.sessions");',
      "const kind = \"title\";",
      // Template-literal key — forbidden by the 04 §4 literal-only contract.
      "export const a = tSessions(`s1.${kind}`);",
    ].join("\n");
    const root = makeFixture({ code: computedCode, zh: FULL_MESSAGES, en: FULL_MESSAGES });

    const result = runVerifier(root);

    expect(result.status).not.toBe(0);
    expect(result.stderr).toContain("computed/template-literal key");
  });

  it("rejects a t() call with no useTranslations binding in the file", () => {
    const orphanCode = ['export const a = t("s1.title");'].join("\n");
    const root = makeFixture({ code: orphanCode, zh: FULL_MESSAGES, en: FULL_MESSAGES });

    const result = runVerifier(root);

    expect(result.status).not.toBe(0);
    expect(result.stderr).toContain("no useTranslations('<ns>') binding");
  });
});
