/**
 * Fixture-driven tests for scripts/verify-packaging.mjs.
 *
 * Each test writes a fake out/index.html (or omits it) and runs the
 * verifier via `node scripts/verify-packaging.mjs <root>`. Exit codes
 * and stderr are asserted for real — a hidden string must not pass.
 */
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterAll, describe, expect, it } from "vitest";

const script = join(dirname(fileURLToPath(import.meta.url)), "verify-packaging.mjs");
const tempRoots = [];

function makeRoot(html) {
  const root = mkdtempSync(join(tmpdir(), "verify-packaging-fixture-"));
  tempRoots.push(root);
  if (html !== null) {
    mkdirSync(join(root, "out"), { recursive: true });
    writeFileSync(join(root, "out", "index.html"), html);
  }
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

describe("verify-packaging.mjs document metadata gate", () => {
  it("exits nonzero when out/index.html is missing", () => {
    const root = makeRoot(null);
    const result = runVerifier(root);
    expect(result.status).not.toBe(0);
    expect(result.stderr).toContain("missing");
    expect(result.stderr).toContain("out/index.html");
  });

  it("exits nonzero when the export has no product marker", () => {
    const result = runVerifier(
      makeRoot("<!DOCTYPE html><html><head></head><body></body></html>"),
    );
    expect(result.status).not.toBe(0);
    expect(result.stderr).toContain("CAL Night Console");
    expect(result.stderr).toContain("application-name");
  });

  it("rejects a hidden or test-only string that is not document metadata", () => {
    const hidden = [
      "<!DOCTYPE html><html><head></head><body>",
      "<!-- CAL Night Console -->",
      '<div hidden="true">CAL Night Console</div>',
      '<span data-testid="CAL Night Console">CAL Night Console</span>',
      "</body></html>",
    ].join("");
    const result = runVerifier(makeRoot(hidden));
    expect(result.status).not.toBe(0);
    expect(result.stderr).toContain("no document title or application-name");
  });

  it("accepts a real <title> product marker", () => {
    const result = runVerifier(
      makeRoot(
        "<!DOCTYPE html><html><head><title>CAL Night Console</title></head><body></body></html>",
      ),
    );
    expect(result.status).toBe(0);
    expect(result.stdout).toContain("CAL Night Console");
    expect(result.stdout).toContain("document metadata");
  });

  it("accepts a real application-name product marker", () => {
    const result = runVerifier(
      makeRoot(
        '<!DOCTYPE html><html><head><meta name="application-name" content="CAL Night Console"/></head><body></body></html>',
      ),
    );
    expect(result.status).toBe(0);
    expect(result.stdout).toContain("OK");
  });

  it("accepts Next App Router flight-serialized title metadata", () => {
    const flight =
      '<script>self.__next_f.push([1,"[\\"$\\",\\"title\\",null,{\\"children\\":\\"CAL Night Console\\"}]\\n"])</script>';
    const result = runVerifier(makeRoot(`<!DOCTYPE html><html><head></head><body>${flight}</body></html>`));
    expect(result.status).toBe(0);
    expect(result.stdout).toContain("OK");
  });
});
