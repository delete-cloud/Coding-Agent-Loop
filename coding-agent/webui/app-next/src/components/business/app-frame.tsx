"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { Composer } from "@/components/business/composer";
import { DetailsPane } from "@/components/business/details-pane";
import { Rail } from "@/components/business/rail";
import { SessionBar } from "@/components/business/session-bar";
import { SettingsPanel, type SettingsClient } from "@/components/business/settings-panel";
import { Sidebar, type SessionItem } from "@/components/business/sidebar";
import {
  Timeline,
  TrajectoryLedger,
  type TimelineProps,
  type TimelineStatus,
} from "@/components/business/timeline";
import { Button } from "@/components/ui/button";
import {
  ConnectedChatProvider,
  healthForStatus,
  useConnectedChat,
  useConnectedChatServices,
  useSessionCatalog,
  type ConnectedChatServices,
  type SessionCatalogClient,
} from "@/hooks/use-connected-chat";
import type { RuntimeConfigPatch } from "@/lib/connected-chat/client";
import type { ConnectedChatStatus } from "@/lib/connected-chat/controller";
import type { ChatSessionSummary } from "@/lib/connected-chat/wire";
import {
  DEFAULT_SESSION_DEFAULTS,
  SETTINGS_LS_KEY,
  formatProviderAccountLabel,
  formatProviderModel,
  errorMessageOf,
  isTapeReboundError,
  loadSessionDefaults,
  persistSessionDefaults,
  resolveProviderAccount,
} from "@/lib/session-settings";
import { applyTheme, readStoredTheme, toggleTheme, type Theme } from "@/lib/theme";

/** Mid-tier band where the details pane becomes an overlay (02 §4.2).
 *  Dual-bounded on purpose: <768px is out of scope and must stay untouched. */
const MID_TIER_QUERY = "(min-width: 768px) and (max-width: 1143px)";

function isBuiltInSessionDefault(defaults: typeof DEFAULT_SESSION_DEFAULTS): boolean {
  return (
    defaults.provider === DEFAULT_SESSION_DEFAULTS.provider &&
    defaults.model === DEFAULT_SESSION_DEFAULTS.model &&
    defaults.base_url === DEFAULT_SESSION_DEFAULTS.base_url
  );
}

/** Titled catalog summary → sidebar row. Untitled sessions are empty/unsent
 *  drafts; a first send may supply a separate local stand-in while the live
 *  catalog omits them. The meta line is the provider · model pair, never the
 *  raw session id. */
function toSidebarSession(summary: ChatSessionSummary): SessionItem | null {
  const title = summary.title;
  if (typeof title !== "string" || title.trim().length === 0) return null;
  return {
    id: summary.session_id,
    title,
    meta: formatProviderModel(
      sessionField(summary, "provider_name"),
      sessionField(summary, "model_name"),
    ),
  };
}

function sessionField(session: ChatSessionSummary, key: "provider_name" | "model_name"): string {
  const value = session[key];
  return typeof value === "string" ? value : "";
}

function isSettingsClient(client: SessionCatalogClient): client is SessionCatalogClient & SettingsClient {
  return (
    "updateRuntimeConfig" in client &&
    "listProviderModels" in client &&
    "listOAuthAccounts" in client &&
    "listCodexFlows" in client &&
    "startCodexOAuth" in client &&
    "getCodexFlow" in client &&
    "cancelCodexFlow" in client &&
    "deleteOAuthAccount" in client
  );
}

/** Controller transport status → view-level timeline status. Exhaustive; the
 *  active work states (following/sending/cancelling/idle) render as "ready"
 *  because the timeline itself is usable in all of them. */
function timelineStatusFor(status: ConnectedChatStatus): TimelineStatus {
  switch (status) {
    case "loading":
      return "loading";
    case "error":
      return "error";
    case "reconnecting":
      return "reconnecting";
    case "replay_required":
      return "replay_required";
    case "idle":
    case "following":
    case "sending":
    case "cancelling":
      return "ready";
  }
}

export interface AppFrameProps {
  /** Test seam: injected connected-chat services, forwarded to the provider.
   *  Omitted in production, where the provider creates same-origin services
   *  on mount (never during the static prerender). */
  services?: ConnectedChatServices;
}

/**
 * AppFrame — the Slice 1 physical shell (02 §2), wired to connected chat.
 * The provider boundary lives HERE (page/layout wrapping is frozen): with no
 * injected services it creates real same-origin services on mount, so the
 * static prerender always renders AppFrameView's no-services loading state.
 */
export function AppFrame({ services }: AppFrameProps) {
  return (
    <ConnectedChatProvider services={services}>
      <AppFrameView />
    </ConnectedChatProvider>
  );
}

/**
 * AppFrameView — the frame body, a thin adapter over the connected-chat
 * hooks. Markup/hierarchy is unchanged from the static shell; only prop
 * values became data-driven.
 *
 * Selection contract:
 *  - the URL (`?session=`) is the durable selection authority: row clicks and
 *    session creation navigate, browser back/forward lands here as a search
 *    param change, and an effect forwards the normalized id to the controller
 *    (stale generations are owned and discarded by the controller itself);
 *  - a newly created session is selected locally during the router gap, then
 *    remains visible until its titled catalog summary arrives;
 *  - normalization: a valid URL id wins, otherwise the first titled catalog
 *    session; pending rows never become an absent-query fallback;
 *  - no services (static prerender) renders a safe loading shell: sidebar and
 *    timeline loading notes, static composer, neutral rail dot.
 *
 * Details lifecycle (02 §4, the single contract):
 *  - closed by default, closed on every session switch (`?session=` change),
 *    never persisted (no localStorage);
 *  - closed = collapsed (width/visibility/pointer-events + inert), the subtree
 *    stays mounted — never display:none;
 *  - Esc closes only when focus is inside the pane, then returns focus to the
 *    SessionBar toggle; closing via the toggle also leaves focus on the toggle;
 *  - click-away listeners live on EXACTLY two surfaces — .timeline-scroll and
 *    .composer-slot — and only close the pane while the overlay is open and the
 *    mid-tier media query matches; clicking the SessionBar (incl. the toggle)
 *    or the details pane itself never closes it; click-away never moves focus.
 */
export function AppFrameView() {
  const tConversation = useTranslations("conversation");
  const services = useConnectedChatServices();
  const catalog = useSessionCatalog(services === null ? null : services.catalog);
  const chat = useConnectedChat();

  const searchParams = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();

  const sessionParam = searchParams.get("session");

  // A live catalog omits a new session until its first prompt supplies a title.
  // Keep the complete local row so both visibility and provider/model metadata
  // survive that gap.
  const [pendingSessions, setPendingSessions] = useState<
    Readonly<Record<string, SessionItem>>
  >({});
  const [pendingSelectionId, setPendingSelectionId] = useState<string | null>(null);
  const sessions = useMemo(() => {
    const catalogIds = new Set(catalog.sessions.map((summary) => summary.session_id));
    const catalogRows = catalog.sessions
      .map(
        (summary) =>
          toSidebarSession(summary) ?? pendingSessions[summary.session_id] ?? null,
      )
      .filter((item): item is SessionItem => item !== null);
    const omittedRows = Object.values(pendingSessions).filter(
      (pending) => !catalogIds.has(pending.id),
    );
    return [...omittedRows, ...catalogRows];
  }, [catalog.sessions, pendingSessions]);

  const defaultCatalogSelection = useMemo(() => {
    for (const summary of catalog.sessions) {
      const item = toSidebarSession(summary);
      if (item !== null) return item;
    }
    return null;
  }, [catalog.sessions]);

  // The local first-send id wins only until the router applies its pushed URL.
  // The pending row itself can outlive that optimistic selection.
  const selection = useMemo(() => {
    if (sessions.length === 0) return null;
    if (pendingSelectionId !== null && pendingSessions[pendingSelectionId] !== undefined) {
      return sessions.find((session) => session.id === pendingSelectionId) ?? null;
    }
    return (
      sessions.find((session) => session.id === sessionParam) ?? defaultCatalogSelection
    );
  }, [
    defaultCatalogSelection,
    pendingSelectionId,
    pendingSessions,
    sessions,
    sessionParam,
  ]);
  const selectedId = selection === null ? null : selection.id;

  useEffect(() => {
    if (pendingSelectionId !== null && sessionParam === pendingSelectionId) {
      setPendingSelectionId(null);
    }
  }, [pendingSelectionId, sessionParam]);

  useEffect(() => {
    const titledPendingIds = new Set(
      catalog.sessions
        .filter(
          (summary) =>
            pendingSessions[summary.session_id] !== undefined &&
            typeof summary.title === "string" &&
            summary.title.trim().length > 0,
        )
        .map((summary) => summary.session_id),
    );
    if (titledPendingIds.size === 0) return;
    setPendingSessions((previous) =>
      Object.fromEntries(
        Object.entries(previous).filter(([id]) => !titledPendingIds.has(id)),
      ),
    );
    setPendingSelectionId((previous) =>
      previous !== null && titledPendingIds.has(previous) ? null : previous,
    );
  }, [catalog.sessions, pendingSessions]);

  // Forward the normalized selection to the controller. Fires on every
  // selectedId change — catalog load, back/forward param changes, and
  // create-then-navigate all arrive through this single path.
  useEffect(() => {
    if (selectedId === null || services === null) return;
    const controller = services.controller;
    if (controller.getState().sessionId === selectedId) return;
    void controller.selectSession(selectedId);
  }, [selectedId, services]);

  const [detailsOpen, setDetailsOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [conversationView, setConversationView] = useState<"chat" | "trajectory">("chat");
  const [theme, setTheme] = useState<Theme>("dark");
  const [defaults, setDefaults] = useState(loadSessionDefaults);
  // Snapshot storage before account discovery. A later built-in fallback write
  // must not block OAuth, while an explicit non-default choice still must.
  const defaultsPersistedAtDiscoveryRef = useRef(true);
  const nonDefaultDefaultsChosenRef = useRef(false);
  const [apiKey, setApiKey] = useState("");
  const [oauthAccounts, setOauthAccounts] = useState<Array<{ provider: string; label: string }>>([]);
  const [composerModels, setComposerModels] = useState<string[]>([]);
  const [composerModelStatus, setComposerModelStatus] = useState<"loading" | "ready">("ready");
  const [runtimeOverlay, setRuntimeOverlay] = useState<{
    sessionId: string;
    provider: string;
    model: string;
    baseUrl: string;
  } | null>(null);
  const [createError, setCreateError] = useState("");
  // New Session stays local until its first prompt is sent.
  const [draftActive, setDraftActive] = useState(false);
  const [draftPrompt, setDraftPrompt] = useState("");
  const [draftBusy, setDraftBusy] = useState(false);
  const openRef = useRef(detailsOpen);
  openRef.current = detailsOpen;
  // Invalidating this generation abandons an in-flight create continuation.
  const draftGenerationRef = useRef(0);

  const timelineScrollRef = useRef<HTMLDivElement>(null);
  const composerSlotRef = useRef<HTMLDivElement>(null);
  const detailsRef = useRef<HTMLElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const stored = readStoredTheme();
    setTheme(stored);
    applyTheme(stored);
  }, []);

  // 02 §4.2: switching sessions unconditionally returns details to closed.
  useEffect(() => {
    setDetailsOpen(false);
    setSettingsOpen(false);
    setConversationView("chat");
    setDraftActive(false);
  }, [sessionParam]);

  useEffect(() => {
    if (services === null || !isSettingsClient(services.catalog)) return;
    try {
      defaultsPersistedAtDiscoveryRef.current =
        localStorage.getItem(SETTINGS_LS_KEY) !== null;
    } catch {
      defaultsPersistedAtDiscoveryRef.current = true;
    }
    let alive = true;
    const client = services.catalog;
    client.listOAuthAccounts().then(
      (accounts) => {
        if (!alive) return;
        const nextAccounts = accounts.map((account) => ({
          provider: account.provider,
          label: account.label,
        }));
        setOauthAccounts(nextAccounts);
        if (
          defaultsPersistedAtDiscoveryRef.current ||
          nonDefaultDefaultsChosenRef.current ||
          nextAccounts.length === 0
        ) {
          return;
        }

        const providers = nextAccounts.map((account) => account.provider);
        const provider = resolveProviderAccount("codex", providers);
        void client.listProviderModels(provider).then(
          (listed) => {
            if (
              !alive ||
              defaultsPersistedAtDiscoveryRef.current ||
              nonDefaultDefaultsChosenRef.current ||
              listed.source !== "live"
            ) {
              return;
            }
            const model = listed.models[0];
            if (model === undefined) return;
            // Account discovery chooses an in-memory first-run default only.
            setDefaults((previous) => ({ ...previous, provider, model }));
          },
          () => {},
        );
      },
      () => {
        if (alive) setOauthAccounts([]);
      },
    );
    return () => {
      alive = false;
    };
  }, [services]);

  const oauthProviders = useMemo(
    () => oauthAccounts.map((account) => account.provider),
    [oauthAccounts],
  );

  useEffect(() => {
    const midTier = window.matchMedia(MID_TIER_QUERY);
    const onClickAway = () => {
      if (openRef.current && midTier.matches) {
        setDetailsOpen(false);
      }
    };
    const timelineScroll = timelineScrollRef.current;
    const composerSlot = composerSlotRef.current;
    timelineScroll?.addEventListener("click", onClickAway);
    composerSlot?.addEventListener("click", onClickAway);
    return () => {
      timelineScroll?.removeEventListener("click", onClickAway);
      composerSlot?.removeEventListener("click", onClickAway);
    };
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (
        event.key === "Escape" &&
        openRef.current &&
        detailsRef.current?.contains(document.activeElement)
      ) {
        setDetailsOpen(false);
        toggleRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  const navigateToSession = (id: string, keepPendingSelection = false) => {
    setDraftActive(false);
    const pendingPushHasNotLanded =
      pendingSelectionId === id && sessionParam !== pendingSelectionId;
    const keepOptimisticSelection = keepPendingSelection || pendingPushHasNotLanded;
    setPendingSelectionId(keepOptimisticSelection ? id : null);
    if (id === selectedId && !pendingPushHasNotLanded) return;
    const params = new URLSearchParams(searchParams.toString());
    params.set("session", id);
    router.push(`${pathname}?${params.toString()}`);
  };

  /** New Session opens a local draft: no POST, no catalog row, live composer. */
  const startDraft = () => {
    setCreateError("");
    setDraftPrompt("");
    setDraftActive(true);
  };

  const cancelDraft = () => {
    draftGenerationRef.current += 1;
    setDraftBusy(false);
  };

  /** First send creates and selects the session before starting its prompt. */
  const sendDraft = () => {
    if (services === null || draftBusy) return;
    const prompt = draftPrompt;
    if (prompt.trim().length === 0) return;
    const request = {
      provider: resolveProviderAccount(defaults.provider, oauthProviders),
      model: defaults.model,
      ...(defaults.base_url ? { base_url: defaults.base_url } : {}),
      ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
    };
    const generation = ++draftGenerationRef.current;
    setCreateError("");
    setDraftBusy(true);
    void (async () => {
      try {
        const id = await catalog.createSession(request);
        const pendingSession = {
          id,
          title: prompt.trim().split("\n")[0] ?? "",
          meta: formatProviderModel(request.provider, request.model),
        };
        const closeAbandonedSession = async () => {
          try {
            await services.catalog.closeSession(id);
            setPendingSessions((previous) => {
              if (previous[id] === undefined) return previous;
              const remaining = { ...previous };
              delete remaining[id];
              return remaining;
            });
            catalog.refresh();
          } catch (error) {
            setPendingSessions((previous) => ({ ...previous, [id]: pendingSession }));
            setCreateError(errorMessageOf(error));
          }
        };
        if (generation !== draftGenerationRef.current) {
          await closeAbandonedSession();
          return;
        }
        setRuntimeOverlay({
          sessionId: id,
          provider: request.provider,
          model: request.model,
          baseUrl: request.base_url ?? "",
        });
        setPendingSessions((previous) => ({ ...previous, [id]: pendingSession }));
        setDraftActive(false);
        setDraftPrompt("");
        navigateToSession(id, true);
        await services.controller.selectSession(id);
        if (
          generation !== draftGenerationRef.current ||
          services.controller.getState().sessionId !== id
        ) {
          await closeAbandonedSession();
          return;
        }
        await services.controller.send(prompt, crypto.randomUUID());
        if (generation !== draftGenerationRef.current) return;
        catalog.refresh();
      } catch (error) {
        if (generation !== draftGenerationRef.current) return;
        setCreateError(errorMessageOf(error));
      } finally {
        if (generation === draftGenerationRef.current) setDraftBusy(false);
      }
    })();
  };

  const applyRuntime = async (patch: RuntimeConfigPatch) => {
    const summary =
      draftActive || selectedId === null
        ? null
        : (catalog.sessions.find((session) => session.session_id === selectedId) ?? null);
    const sessionProvider =
      runtimeOverlay && selectedId === runtimeOverlay.sessionId
        ? runtimeOverlay.provider
        : summary
          ? sessionField(summary, "provider_name")
          : "";
    const sessionModel =
      runtimeOverlay && selectedId === runtimeOverlay.sessionId
        ? runtimeOverlay.model
        : summary
          ? sessionField(summary, "model_name")
          : "";
    const sessionBaseUrl =
      runtimeOverlay && selectedId === runtimeOverlay.sessionId
        ? runtimeOverlay.baseUrl
        : summary
          ? typeof summary.base_url === "string"
            ? summary.base_url
            : ""
          : defaults.base_url;
    const nextDefaults = {
      provider: resolveProviderAccount(
        patch.provider ?? (sessionProvider || defaults.provider),
        oauthProviders,
      ),
      model: patch.model ?? (sessionModel || defaults.model),
      base_url: patch.base_url === undefined ? sessionBaseUrl : (patch.base_url ?? ""),
    };
    const resolvedPatch: RuntimeConfigPatch = { ...patch };
    if (patch.provider !== undefined) {
      resolvedPatch.provider = resolveProviderAccount(patch.provider, oauthProviders);
    }
    try {
      if (!draftActive && selectedId !== null && services !== null && isSettingsClient(services.catalog)) {
        const updated = await services.catalog.updateRuntimeConfig(selectedId, resolvedPatch);
        const persisted = {
          provider: updated.provider_name ?? nextDefaults.provider,
          model: updated.model_name ?? nextDefaults.model,
          base_url: updated.base_url ?? "",
        };
        setRuntimeOverlay({
          sessionId: updated.session_id,
          provider: persisted.provider,
          model: persisted.model,
          baseUrl: persisted.base_url,
        });
        nonDefaultDefaultsChosenRef.current = !isBuiltInSessionDefault(persisted);
        persistSessionDefaults(persisted);
        setDefaults(persisted);
        setApiKey(patch.api_key ?? "");
        return;
      }
      nonDefaultDefaultsChosenRef.current = !isBuiltInSessionDefault(nextDefaults);
      persistSessionDefaults(nextDefaults);
      setDefaults(nextDefaults);
      setApiKey(patch.api_key ?? "");
    } catch (error) {
      if (isTapeReboundError(error)) {
        nonDefaultDefaultsChosenRef.current = !isBuiltInSessionDefault(nextDefaults);
        persistSessionDefaults(nextDefaults);
        setDefaults(nextDefaults);
        setApiKey(patch.api_key ?? "");
      }
      throw error;
    }
  };

  const selectedSummary =
    selectedId === null
      ? null
      : (catalog.sessions.find((session) => session.session_id === selectedId) ?? null);
  const liveProvider =
    runtimeOverlay && selectedId === runtimeOverlay.sessionId
      ? runtimeOverlay.provider
      : selectedSummary
        ? sessionField(selectedSummary, "provider_name")
        : "";
  const liveModel =
    runtimeOverlay && selectedId === runtimeOverlay.sessionId
      ? runtimeOverlay.model
      : selectedSummary
        ? sessionField(selectedSummary, "model_name")
        : "";
  const liveBaseUrl =
    runtimeOverlay && selectedId === runtimeOverlay.sessionId
      ? runtimeOverlay.baseUrl
      : selectedSummary
        ? typeof selectedSummary.base_url === "string"
          ? selectedSummary.base_url
          : ""
        : defaults.base_url;

  useEffect(() => {
    const provider = (draftActive ? defaults.provider : liveProvider || defaults.provider).trim();
    if (!provider || services === null || !isSettingsClient(services.catalog)) {
      setComposerModels([]);
      setComposerModelStatus("ready");
      return;
    }
    setComposerModelStatus("loading");
    const controller = new AbortController();
    services.catalog.listProviderModels(provider, controller.signal).then(
      (listed) => {
        setComposerModels(listed.source === "live" ? listed.models : []);
        setComposerModelStatus("ready");
      },
      () => {
        setComposerModels([]);
        setComposerModelStatus("ready");
      },
    );
    return () => controller.abort();
  }, [draftActive, liveProvider, defaults.provider, services]);

  const chatForSelection =
    chat !== null && selectedId !== null && chat.state.sessionId === selectedId ? chat : null;

  // A local draft displays the defaults that its first send will create with.
  const shownProvider = draftActive ? defaults.provider : liveProvider;
  const shownModel = draftActive ? defaults.model : liveModel;
  const shownBaseUrl = draftActive ? defaults.base_url : liveBaseUrl;

  let timelineProps: TimelineProps;
  if (draftActive) {
    timelineProps = { messages: [], status: "ready" };
  } else if (chatForSelection !== null) {
    timelineProps = {
      messages: chatForSelection.messages,
      status: timelineStatusFor(chatForSelection.state.status),
      error: chatForSelection.state.error,
      replayReason:
        chatForSelection.state.replayReason === null
          ? undefined
          : chatForSelection.state.replayReason,
    };
  } else if (selectedId === null && catalog.status === "ready") {
    timelineProps = { messages: [], status: "ready" };
  } else {
    timelineProps = { messages: [], status: "loading" };
  }

  const catalogState =
    catalog.status === "error"
      ? ({ status: "error", onRetry: catalog.refresh } as const)
      : ({ status: catalog.status } as const);

  return (
    <div className="appframe">
      <Rail health={chat === null ? undefined : healthForStatus(chat.state.status)} />
      <Sidebar
        sessions={sessions}
        selectedId={draftActive ? "" : (selectedId ?? "")}
        onSelect={navigateToSession}
        onCreateSession={services === null ? undefined : startDraft}
        createPending={catalog.createPending}
        createError={createError}
        {...catalogState}
      />
      <main className="conversation">
        <SessionBar
          title={draftActive || selection === null ? "" : selection.title}
          detailsOpen={detailsOpen}
          onToggleDetails={() => setDetailsOpen((open) => !open)}
          toggleRef={toggleRef}
          chatStatus={
            draftActive || chatForSelection === null ? undefined : chatForSelection.state.status
          }
          providerModel={formatProviderModel(
            formatProviderAccountLabel(shownProvider, oauthAccounts),
            shownModel,
          )}
          theme={theme}
          onOpenSettings={() => setSettingsOpen((open) => !open)}
          onToggleTheme={() => {
            const next = toggleTheme(theme);
            setTheme(next);
            applyTheme(next);
          }}
        />
        {settingsOpen && services !== null && isSettingsClient(services.catalog) ? (
          <SettingsPanel
            open
            sessionId={draftActive ? null : selectedId}
            providerName={shownProvider || defaults.provider}
            modelName={shownModel || defaults.model}
            currentBaseUrl={shownBaseUrl}
            accounts={oauthAccounts}
            onAccountsChange={(next) => {
              setOauthAccounts(next.map((account) => ({
                provider: account.provider,
                label: account.label,
              })));
            }}
            client={services.catalog}
            onApply={applyRuntime}
          />
        ) : null}
        <div className="conversation-tabs" role="tablist" aria-label={tConversation("tabsLabel")}>
          <Button
            type="button"
            variant="ghost"
            role="tab"
            className="conversation-tab"
            aria-selected={conversationView === "chat"}
            onClick={() => setConversationView("chat")}
          >
            {tConversation("chat")}
          </Button>
          <Button
            type="button"
            variant="ghost"
            role="tab"
            className="conversation-tab"
            aria-selected={conversationView === "trajectory"}
            onClick={() => setConversationView("trajectory")}
          >
            {tConversation("trajectory")}
          </Button>
        </div>
        <div className="timeline-scroll" ref={timelineScrollRef}>
          {conversationView === "chat" ? (
            <Timeline {...timelineProps} />
          ) : (
            <TrajectoryLedger {...timelineProps} />
          )}
        </div>
        <div className="composer-slot" ref={composerSlotRef}>
          {draftActive ? (
            <Composer
              draft={draftPrompt}
              onDraftChange={setDraftPrompt}
              onSend={sendDraft}
              onCancel={cancelDraft}
              onResume={() => {}}
              onReload={() => {}}
              status={draftBusy ? "sending" : "idle"}
              canResume={false}
              model={defaults.model}
              onModelChange={(model) => {
                void applyRuntime({ model });
              }}
              provider={resolveProviderAccount(defaults.provider, oauthProviders)}
              onProviderChange={(nextProvider) => {
                void applyRuntime({ provider: nextProvider });
              }}
              oauthProviders={oauthProviders}
              accounts={oauthAccounts}
              modelOptions={composerModels}
              modelStatus={composerModelStatus}
            />
          ) : chatForSelection === null ? (
            <Composer />
          ) : (
            <Composer
              draft={chatForSelection.state.draft}
              onDraftChange={chatForSelection.setDraft}
              onSend={chatForSelection.send}
              onCancel={chatForSelection.cancel}
              onResume={chatForSelection.resume}
              onReload={chatForSelection.reload}
              status={chatForSelection.state.status}
              canResume={chatForSelection.canResume}
              model={liveModel || defaults.model}
              onModelChange={(model) => {
                void applyRuntime({ model });
              }}
              provider={resolveProviderAccount(liveProvider || defaults.provider, oauthProviders)}
              onProviderChange={(nextProvider) => {
                void applyRuntime({ provider: nextProvider });
              }}
              oauthProviders={oauthProviders}
              accounts={oauthAccounts}
              modelOptions={composerModels}
              modelStatus={composerModelStatus}
            />
          )}
        </div>
      </main>
      <DetailsPane
        open={detailsOpen}
        paneRef={detailsRef}
        provider={shownProvider}
        model={shownModel}
      />
    </div>
  );
}
