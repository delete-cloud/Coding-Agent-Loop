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

/** Catalog summary → sidebar row. `title` is nullable by contract: a session
 *  without one is identified by its stable id (absence is a valid state). */
function toSessionItem(summary: ChatSessionSummary): SessionItem {
  return {
    id: summary.session_id,
    title: summary.title ?? summary.session_id,
    meta: summary.session_id,
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
 *  - the URL (`?session=`) is the ONLY selection authority: row clicks and
 *    session creation navigate, browser back/forward lands here as a search
 *    param change, and an effect forwards the normalized id to the
 *    controller (stale generations are owned and discarded by the controller
 *    itself — the frame never reconciles them);
 *  - normalization: a valid catalog id wins, otherwise the first session; no
 *    catalog sessions → no selection (empty states, inert static composer);
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

  const sessions = useMemo(() => catalog.sessions.map(toSessionItem), [catalog.sessions]);

  // URL-led selection with valid-id normalization: a valid param wins, an
  // absent/invalid one falls back to the first catalog session, and an empty
  // catalog means no selection at all.
  const selection = useMemo(() => {
    if (sessions.length === 0) return null;
    return sessions.find((s) => s.id === sessionParam) ?? sessions[0];
  }, [sessions, sessionParam]);
  const selectedId = selection === null ? null : selection.id;

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
  const [apiKey, setApiKey] = useState("");
  const [oauthAccounts, setOauthAccounts] = useState<Array<{ provider: string; label: string }>>([]);
  const [composerModels, setComposerModels] = useState<string[]>([]);
  const [composerModelStatus, setComposerModelStatus] = useState<"loading" | "ready">("ready");
  const [runtimeOverlay, setRuntimeOverlay] = useState<{
    sessionId: string;
    provider: string;
    model: string;
  } | null>(null);
  const [createError, setCreateError] = useState("");
  const openRef = useRef(detailsOpen);
  openRef.current = detailsOpen;

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
  }, [sessionParam]);

  useEffect(() => {
    if (services === null || !isSettingsClient(services.catalog)) return;
    let alive = true;
    services.catalog
      .listOAuthAccounts()
      .then((accounts) => {
        if (alive) {
          setOauthAccounts(accounts.map((account) => ({
            provider: account.provider,
            label: account.label,
          })));
        }
      })
      .catch(() => {
        if (alive) setOauthAccounts([]);
      });
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

  const navigateToSession = (id: string) => {
    if (id === selectedId) return;
    const params = new URLSearchParams(searchParams.toString());
    params.set("session", id);
    router.push(`${pathname}?${params.toString()}`);
  };

  const createSession = () => {
    const request = {
      provider: resolveProviderAccount(defaults.provider, oauthProviders),
      model: defaults.model,
      ...(defaults.base_url ? { base_url: defaults.base_url } : {}),
      ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
    };
    setCreateError("");
    void catalog.createSession(request).then(
      (id) => {
        setRuntimeOverlay({
          sessionId: id,
          provider: request.provider,
          model: request.model,
        });
        navigateToSession(id);
      },
      (error: unknown) => {
        setCreateError(errorMessageOf(error));
      },
    );
  };

  const applyRuntime = async (patch: RuntimeConfigPatch) => {
    const nextDefaults = {
      provider: resolveProviderAccount(patch.provider ?? defaults.provider, oauthProviders),
      model: patch.model ?? defaults.model,
      base_url: patch.base_url ?? defaults.base_url,
    };
    const resolvedPatch: RuntimeConfigPatch = { ...patch };
    if (patch.provider !== undefined) {
      resolvedPatch.provider = resolveProviderAccount(patch.provider, oauthProviders);
    }
    try {
      if (selectedId !== null && services !== null && isSettingsClient(services.catalog)) {
        const updated = await services.catalog.updateRuntimeConfig(selectedId, resolvedPatch);
        setRuntimeOverlay({
          sessionId: updated.session_id,
          provider: updated.provider_name ?? nextDefaults.provider,
          model: updated.model_name ?? nextDefaults.model,
        });
        setApiKey(patch.api_key ?? "");
        return;
      }
      persistSessionDefaults(nextDefaults);
      setDefaults(nextDefaults);
      setApiKey(patch.api_key ?? "");
    } catch (error) {
      if (isTapeReboundError(error)) {
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

  useEffect(() => {
    const provider = (liveProvider || defaults.provider).trim();
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
  }, [liveProvider, defaults.provider, services]);

  const chatForSelection =
    chat !== null && selectedId !== null && chat.state.sessionId === selectedId ? chat : null;

  let timelineProps: TimelineProps;
  if (chatForSelection !== null) {
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
        selectedId={selectedId ?? ""}
        onSelect={navigateToSession}
        onCreateSession={services === null ? undefined : createSession}
        createPending={catalog.createPending}
        createError={createError}
        {...catalogState}
      />
      <main className="conversation">
        <SessionBar
          title={selection === null ? "" : selection.title}
          detailsOpen={detailsOpen}
          onToggleDetails={() => setDetailsOpen((open) => !open)}
          toggleRef={toggleRef}
          chatStatus={chatForSelection === null ? undefined : chatForSelection.state.status}
          providerModel={formatProviderModel(
            formatProviderAccountLabel(liveProvider, oauthAccounts),
            liveModel,
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
            sessionId={selectedId}
            providerName={liveProvider || defaults.provider}
            modelName={liveModel || defaults.model}
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
          {chatForSelection === null ? (
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
        provider={liveProvider}
        model={liveModel}
      />
    </div>
  );
}
