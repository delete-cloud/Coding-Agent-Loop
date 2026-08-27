// Shared helpers for AppFrame-level tests: intl wrapping plus the injected
// connected-chat services pair. The controller's reconnect sleep resolves
// "aborted" immediately so no stray backoff timers leak across tests; the
// reconnecting state itself is still asserted (controller tests own the
// recovery loop).

import { act, render } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactElement } from "react";

import zhMessages from "../../messages/zh.json";
import { flush, makeSnapshot, waitUntil, FakeBackend } from "./connected-chat-fake";
import { ConnectedChatController } from "@/lib/connected-chat/controller";
import type { ConnectedChatServices } from "@/hooks/use-connected-chat";
import type { ChatEventEnvelope, ChatSessionSummary } from "@/lib/connected-chat/wire";

export function withIntl(element: ReactElement) {
  return (
    <NextIntlClientProvider locale="zh" messages={zhMessages}>
      {element}
    </NextIntlClientProvider>
  );
}

export { render };

/** A FakeBackend plus a real controller over it, ready to inject as services. */
export function fakeServices(backend = new FakeBackend()) {
  const controller = new ConnectedChatController(backend, {
    sleep: () => Promise.resolve(false),
  });
  const services: ConnectedChatServices = { controller, catalog: backend };
  return { backend, controller, services };
}

/** Resolve the most recent listSessions call; fails fast when none is pending. */
export async function resolveCatalog(backend: FakeBackend, sessions: ChatSessionSummary[]) {
  const pending = backend.lists.at(-1);
  if (!pending) throw new Error("expected a pending listSessions call");
  const snapshotsBefore = backend.snapshotCalls.length;
  await act(async () => {
    pending.resolve({ contract_version: "1.0.0", sessions });
    await flush();
  });
  if (sessions.length === 0) return;
  await waitUntil(
    () => backend.snapshotCalls.length > snapshotsBefore,
    "catalog selection to request a snapshot",
  );
}

/** Resolve the most recent snapshot call; fails fast when none is pending. */
export async function resolveSnapshot(
  backend: FakeBackend,
  sessionId: string,
  events: ChatEventEnvelope[] = [],
) {
  const pending = backend.snapshots.at(-1);
  if (!pending) throw new Error("expected a pending snapshot call");
  const followsBefore = backend.followCalls.length;
  await act(async () => {
    pending.resolve(makeSnapshot(sessionId, events));
    await flush();
  });
  await waitUntil(
    () => backend.followCalls.length > followsBefore,
    "snapshot to start a follow stream",
  );
}
