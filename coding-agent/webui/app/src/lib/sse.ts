// Parse an SSE byte stream into {event, data} frames.
//
// The agent server streams SSE over POST (not GET), so the native EventSource
// API cannot be used — we read the fetch ReadableStream directly. sse-starlette
// terminates lines with CRLF, so we normalise \r out before splitting on the
// blank-line frame boundary.

export interface RawSSE {
  event: string;
  data: string;
}

export async function* parseSSE(
  stream: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<RawSSE> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  const flushFrame = (frame: string): RawSSE | null => {
    let event = "message";
    let data = "";
    for (const line of frame.split("\n")) {
      if (line.startsWith(":")) continue; // comment / heartbeat
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data += line.slice(5).trim();
    }
    return data ? { event, data } : null;
  };

  try {
    while (true) {
      if (signal?.aborted) break;
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true }).replace(/\r/g, "");
      let idx: number;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const parsed = flushFrame(frame);
        if (parsed) yield parsed;
      }
    }
    // Flush any trailing frame not terminated by a blank line.
    const tail = flushFrame(buf.trim());
    if (tail) yield tail;
  } finally {
    reader.releaseLock();
  }
}
