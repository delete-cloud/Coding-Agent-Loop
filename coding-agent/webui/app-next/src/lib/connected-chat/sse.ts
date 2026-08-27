// Minimal SSE frame parser over a fetch ReadableStream.
//
// The contract streams SSE over both GET (follow) and POST (prompt/resume),
// so the native EventSource API cannot be used — we read the response body
// directly. Handles CRLF and LF endings, frames split across arbitrary chunk
// boundaries, multiline data fields (joined with "\n" per the SSE spec), and
// comment/heartbeat lines. A trailing frame without the final blank line is
// flushed at EOF. EOF itself is a transport fact, not a terminal signal: the
// generator simply completes.

export interface SseFrame {
  event: string;
  data: string;
  /** The SSE `id` field (decimal session_seq for chat events), if present. */
  id: string | null;
}

export async function* parseSseStream(
  stream: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<SseFrame> {
  const reader = stream.getReader();
  const onAbort = () => {
    void reader.cancel().catch(() => undefined);
  };
  if (signal) {
    if (signal.aborted) {
      reader.releaseLock();
      return;
    }
    signal.addEventListener("abort", onAbort, { once: true });
  }

  const decoder = new TextDecoder();
  let buffer = "";

  const flushFrame = (frame: string): SseFrame | null => {
    let event = "message";
    let id: string | null = null;
    const dataLines: string[] = [];
    for (const rawLine of frame.split("\n")) {
      const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
      if (line.startsWith(":")) continue; // comment / heartbeat
      const colon = line.indexOf(":");
      if (colon === -1) continue; // field-name-only line: treated as no value
      const field = line.slice(0, colon);
      // The SSE spec strips a single leading space after the colon.
      const value = line.slice(colon + 1).replace(/^ /, "");
      if (field === "event") event = value;
      else if (field === "data") dataLines.push(value);
      else if (field === "id") id = value;
      // Unknown fields are ignored per the SSE spec.
    }
    if (dataLines.length === 0) return null;
    return { event, data: dataLines.join("\n"), id };
  };

  try {
    while (true) {
      if (signal?.aborted) return;
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary = buffer.search(/\r?\n\r?\n/);
      while (boundary !== -1) {
        const match = buffer.slice(boundary).match(/^\r?\n\r?\n/);
        if (!match) throw new Error("unreachable: boundary matched but separator did not");
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + match[0].length);
        const parsed = flushFrame(frame);
        if (parsed) yield parsed;
        boundary = buffer.search(/\r?\n\r?\n/);
      }
    }
    // Flush a trailing frame not terminated by a blank line. Skipped on abort:
    // a cancelled read can leave a truncated frame that must not be parsed.
    if (!signal?.aborted) {
      const tail = flushFrame(buffer);
      if (tail) yield tail;
    }
  } finally {
    signal?.removeEventListener("abort", onAbort);
    reader.releaseLock();
  }
}
