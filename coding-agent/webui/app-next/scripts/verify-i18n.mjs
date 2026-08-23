#!/usr/bin/env node
/**
 * verify-i18n.mjs — i18n gate for Slice 1 (04 §4).
 *
 * next-intl typed-messages are OFF, so tsc cannot catch missing keys. This
 * script statically scans src/ for translation references and asserts every
 * referenced key exists, non-empty, in BOTH messages/zh.json and
 * messages/en.json.
 *
 * Binding-aware scanning: a reference is a call through an identifier that
 * was bound in the same file via
 *     const <ident> = useTranslations('<namespace>')
 * so aliases like `const tSessions = useTranslations("sidebar.sessions")`
 * followed by tSessions("s1.title") are fully captured. Multiple bindings in
 * one file are all discovered, in file order (deterministic).
 *
 * The 04 §4 contract forbids computed/concatenated keys, so literal-only
 * enforcement applies:
 *   - useTranslations(`...`) template-literal namespace      → gate failure
 *   - <ident>(`...`) / <ident>(expr) non-literal key argument → gate failure
 *   - t('...') with no useTranslations binding in the file    → gate failure
 *
 * Usage: node scripts/verify-i18n.mjs [rootDir]
 *   rootDir defaults to the app root (parent of this script's directory);
 *   tests pass a fixture root instead.
 *
 * Exit code is nonzero on any miss. Runs alongside tsc and vitest
 * (`pnpm verify`); a failing gate means the slice fails.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, extname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = process.argv[2]
  ? resolve(process.argv[2])
  : dirname(dirname(fileURLToPath(import.meta.url)));
const srcDir = join(root, "src");

function* walk(dir) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      yield* walk(full);
    } else if ([".ts", ".tsx"].includes(extname(entry))) {
      yield full;
    }
  }
}

function getPath(obj, path) {
  let node = obj;
  for (const segment of path.split(".")) {
    if (node === null || typeof node !== "object" || !(segment in node)) {
      return undefined;
    }
    node = node[segment];
  }
  return node;
}

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

const zh = JSON.parse(readFileSync(join(root, "messages", "zh.json"), "utf8"));
const en = JSON.parse(readFileSync(join(root, "messages", "en.json"), "utf8"));

// const <ident> = useTranslations('<ns>')   (or ""/() for no namespace)
const bindingPattern =
  /const\s+([A-Za-z_$][\w$]*)\s*=\s*useTranslations\(\s*(?:(['"])([\s\S]*?)\2)?\s*\)/g;
const nsTemplatePattern = /useTranslations\(\s*`/g;
// A bare t(...) call is only legal when `t` is a binding in this file.
const bareTPattern = /\bt\(\s*(['"`])/g;

/** @type {Array<{file:string, fullKey:string}>} */
const references = [];
const failures = [];

for (const file of walk(srcDir)) {
  const code = readFileSync(file, "utf8");
  const rel = relative(root, file);

  for (const match of code.matchAll(nsTemplatePattern)) {
    failures.push(
      `${rel}: computed/template-literal namespace at offset ${match.index} — ` +
        `useTranslations() arguments must be string literals (04 §4)`,
    );
  }

  // Discover every translation binding in this file, in file order.
  /** @type {Array<{ident:string, ns:string}>} */
  const bindings = [];
  for (const match of code.matchAll(bindingPattern)) {
    bindings.push({ ident: match[1], ns: match[3] ?? "" });
  }

  for (const { ident, ns } of bindings) {
    const callPattern = new RegExp(`\\b${escapeRegExp(ident)}\\(`, "g");
    const literalPattern = new RegExp(
      `\\b${escapeRegExp(ident)}\\(\\s*(['"])([\\s\\S]*?)\\1\\s*[,)]`,
      "g",
    );

    const literalOffsets = new Set();
    for (const match of code.matchAll(literalPattern)) {
      literalOffsets.add(match.index);
      references.push({ file: rel, fullKey: ns ? `${ns}.${match[2]}` : match[2] });
    }

    // Any call through this identifier whose first argument is NOT a string
    // literal is a computed key — forbidden by 04 §4.
    for (const match of code.matchAll(callPattern)) {
      if (!literalOffsets.has(match.index)) {
        failures.push(
          `${rel}: computed/template-literal key at offset ${match.index} — ` +
            `${ident}() arguments must be string literals (04 §4)`,
        );
      }
    }
  }

  // t('<key>') without a corresponding `const t = useTranslations(...)`
  // binding in this file cannot be attributed to a namespace.
  if (!bindings.some((b) => b.ident === "t")) {
    for (const match of code.matchAll(bareTPattern)) {
      failures.push(
        `${rel}: t(...) call at offset ${match.index} has no ` +
          `useTranslations('<ns>') binding in this file`,
      );
    }
  }
}

const uniqueKeys = [...new Set(references.map((r) => r.fullKey))];
for (const fullKey of uniqueKeys) {
  for (const [locale, messages] of [
    ["zh", zh],
    ["en", en],
  ]) {
    const value = getPath(messages, fullKey);
    const usedIn = references.filter((r) => r.fullKey === fullKey).map((r) => r.file);
    if (value === undefined) {
      failures.push(
        `messages/${locale}.json: missing key '${fullKey}' (referenced in ${usedIn.join(", ")})`,
      );
    } else if (typeof value !== "string" || value.trim() === "") {
      failures.push(
        `messages/${locale}.json: key '${fullKey}' is empty or not a string (referenced in ${usedIn.join(", ")})`,
      );
    }
  }
}

if (failures.length > 0) {
  console.error(`verify-i18n: FAILED with ${failures.length} problem(s):`);
  for (const failure of failures) {
    console.error(`  - ${failure}`);
  }
  process.exit(1);
}

console.log(
  `verify-i18n: OK — ${uniqueKeys.length} referenced key(s) present and non-empty in both zh and en.`,
);
