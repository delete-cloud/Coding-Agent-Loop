#!/usr/bin/env node
/**
 * verify-packaging.mjs — static-export product-marker gate.
 *
 * After `next build` (`output: 'export'`), out/index.html must carry the
 * stable CAL Night Console identity as document title and/or
 * application-name. Hidden comments, data-testids, and unrelated hashed
 * chunks do not count. This script reads only out/index.html.
 *
 * Usage: node scripts/verify-packaging.mjs [rootDir]
 *   rootDir defaults to the app root (parent of this script's directory).
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const PRODUCT_MARKER = "CAL Night Console";

const root = process.argv[2]
  ? resolve(process.argv[2])
  : dirname(dirname(fileURLToPath(import.meta.url)));
const indexPath = join(root, "out", "index.html");

if (!existsSync(indexPath)) {
  console.error(
    `verify-packaging: missing ${indexPath}. Run \`next build\` first.`,
  );
  process.exit(1);
}

const html = readFileSync(indexPath, "utf8");

if (!hasDocumentProductMarker(html)) {
  console.error(
    `verify-packaging: out/index.html has no document title or application-name equal to "${PRODUCT_MARKER}".`,
  );
  process.exit(1);
}

console.log(
  `verify-packaging: OK — ${PRODUCT_MARKER} present as document metadata in out/index.html`,
);

/**
 * True when the HTML carries the product identity as a document title
 * and/or application-name, including Next App Router flight serialization.
 * A bare string occurrence (comment, hidden node, test id) is not enough.
 */
function hasDocumentProductMarker(source) {
  const html = decodeFlightEscapes(source);
  return hasDocumentTitle(html) || hasApplicationName(html);
}

function decodeFlightEscapes(source) {
  return source.replace(/\\"/g, '"').replace(/\\u0026/g, "&");
}

function hasDocumentTitle(html) {
  if (/<title>\s*CAL Night Console\s*<\/title>/i.test(html)) {
    return true;
  }
  // Next metadata flight node: ["$","title",null,{"children":"CAL Night Console"}]
  return /"\$","title"(?:,[^[]*)?"children":"CAL Night Console"/.test(html);
}

function hasApplicationName(html) {
  if (
    /<meta\b[^>]*\bname=["']application-name["'][^>]*\bcontent=["']CAL Night Console["'][^>]*>/i.test(
      html,
    ) ||
    /<meta\b[^>]*\bcontent=["']CAL Night Console["'][^>]*\bname=["']application-name["'][^>]*>/i.test(
      html,
    )
  ) {
    return true;
  }
  return (
    /"name":"application-name"[^}]*"content":"CAL Night Console"/.test(html) ||
    /"content":"CAL Night Console"[^}]*"name":"application-name"/.test(html)
  );
}
