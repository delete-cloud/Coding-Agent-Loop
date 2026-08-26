"use client";

import { PanelRight, Settings, SunMoon } from "lucide-react";
import { useTranslations } from "next-intl";
import type { RefObject } from "react";

import { Button } from "@/components/ui/button";
import type { ConnectedChatStatus } from "@/lib/connected-chat/controller";

import type { Theme } from "@/lib/theme";

interface SessionBarProps {
  title: string;
  detailsOpen: boolean;
  onToggleDetails: () => void;
  toggleRef: RefObject<HTMLButtonElement | null>;
  /** Connected-chat transport status driving the readout status word. When
   *  omitted (Slice 1 shell), the static placeholder word is rendered. */
  chatStatus?: ConnectedChatStatus;
  /** Live `provider · model` for the selected session. Empty/omitted shows unset. */
  providerModel?: string;
  theme?: Theme;
  onOpenSettings?: () => void;
  onToggleTheme?: () => void;
}

type Translate = (key: string) => string;

/** Exhaustive literal-key mapping; computed keys are forbidden by 04 §4. */
function statusLabel(status: ConnectedChatStatus, t: Translate): string {
  switch (status) {
    case "idle":
      return t("statusIdle");
    case "loading":
      return t("statusLoading");
    case "following":
      return t("statusFollowing");
    case "sending":
      return t("statusSending");
    case "cancelling":
      return t("statusCancelling");
    case "reconnecting":
      return t("statusReconnecting");
    case "replay_required":
      return t("statusReplayRequired");
    case "error":
      return t("statusError");
  }
}

/**
 * SessionBar — a single 40px row (02 §7).
 * "Look" content stays left; "set" controls live in the weakened temporary
 * settings group (interim position until Slice 5); the details toggle sits
 * rightmost with aria-expanded and a real 1px amber border when active.
 * The bar stays in the document flow at z-index 0, so the mid-tier overlay
 * (top: 40px) never covers it and the toggle remains clickable (02 §4.2).
 */
export function SessionBar({
  title,
  detailsOpen,
  onToggleDetails,
  toggleRef,
  chatStatus,
  providerModel,
  theme,
  onOpenSettings,
  onToggleTheme,
}: SessionBarProps) {
  const t = useTranslations("sessionbar");
  const themeLabel =
    theme === "light" ? t("themeToDark") : theme === "dark" ? t("themeToLight") : t("theme");

  return (
    <header className="sessionbar">
      <span className="sessionbar-title">{title}</span>
      <span className="sessionbar-prov">
        {providerModel && providerModel.trim().length > 0
          ? providerModel
          : t("providerModelUnset")}
      </span>
      <div className="spacer" />
      <span className="readout">
        {chatStatus === undefined ? (
          <em>{t("readoutStatus")}</em>
        ) : (
          <span>{statusLabel(chatStatus, t)}</span>
        )}
      </span>
      <div className="temp-controls">
        <Button
          variant="ghost"
          size="icon"
          className="iconbtn"
          title={t("settings")}
          aria-label={t("settings")}
          onClick={onOpenSettings}
        >
          <Settings />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="iconbtn"
          title={themeLabel}
          aria-label={themeLabel}
          onClick={onToggleTheme}
        >
          <SunMoon />
        </Button>
      </div>
      <Button
        variant="ghost"
        size="icon"
        className="iconbtn details-toggle"
        title={t("detailsToggle")}
        aria-expanded={detailsOpen}
        aria-controls="details-pane"
        onClick={onToggleDetails}
        ref={toggleRef}
      >
        <PanelRight />
      </Button>
    </header>
  );
}
