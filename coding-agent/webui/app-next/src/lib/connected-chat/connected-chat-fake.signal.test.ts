import { describe, expect, it } from "vitest";

import { FakeBackend } from "../../../test/helpers/connected-chat-fake";

function abortErrorName(error: unknown): string {
  if (error instanceof DOMException) return error.name;
  if (error instanceof Error) return error.name;
  throw new Error(`expected an Error, received ${String(error)}`);
}

describe("FakeBackend AbortSignal wiring", () => {
  it("aborts a pending follow next() when the caller signal aborts", async () => {
    const backend = new FakeBackend();
    const abort = new AbortController();
    const stream = backend.follow("session-01", "cursor", abort.signal);
    const pending = stream[Symbol.asyncIterator]().next();
    expect(stream.pendingWaiters).toBe(1);
    expect(stream.closed).toBe(false);

    abort.abort();

    await expect(pending).rejects.toSatisfy((error) => abortErrorName(error) === "AbortError");
    expect(stream.closed).toBe(true);
    expect(stream.pendingWaiters).toBe(0);
  });

  it("aborts a pending snapshot when the caller signal aborts", async () => {
    const backend = new FakeBackend();
    const abort = new AbortController();
    const pending = backend.snapshot("session-01", {}, abort.signal);
    expect(backend.snapshots).toHaveLength(1);

    abort.abort();

    await expect(pending).rejects.toSatisfy((error) => abortErrorName(error) === "AbortError");
  });

  it("closes a follow iterator when return() is called", async () => {
    const backend = new FakeBackend();
    const stream = backend.follow("session-01", "cursor");
    const iterator = stream[Symbol.asyncIterator]();
    const pending = iterator.next();
    expect(stream.pendingWaiters).toBe(1);

    const result = await iterator.return?.();
    expect(result).toEqual({ value: undefined, done: true });
    expect(stream.closed).toBe(true);
    await expect(pending).resolves.toEqual({ value: undefined, done: true });
  });
});
