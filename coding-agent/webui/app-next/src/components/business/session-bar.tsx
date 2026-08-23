"use client";

import { PanelRight, Settings, SunMoon } from "lucide-react";
import { useTranslations } from "next-intl";
import type { RefObject } from "react";

import { Button } from "@/components/ui/button";

interface SessionBarProps {
  title: string;
  detailsOpen: boolean;
  onToggleDetails: () => void;
  toggleRef: RefObject<HTMLButtonElement | null>;
}

/**
 * SessionBar — a single 40px row (02 §7).
 * "Look" content stays left; "set" controls live in the weakened temporary
 * settings group (interim position until Slice 5); the details toggle sits
 * rightmost with aria-expanded and a real 1px amber border when active.
 * The bar stays in the document flow at z-index 0, so the mid-tier overlay
 * (top: 40px) never covers it and the toggle remains clickable (02 §4.2).
 */
export function SessionBar({ title, detailsOpen, onToggleDetails, toggleRef }: SessionBarProps) {
  const t = useTranslations("sessionbar");

  return (
    <header className="sessionbar">
      <span className="sessionbar-title">{title}</span>
      <span className="sessionbar-prov">{t("providerModel")}</span>
      <div className="spacer" />
      <span className="readout">
        <em>{t("readoutStatus")}</em>
        {" · "}
        {t("readoutMetrics")}
      </span>
      {/* Temporary settings group (02 §7): placeholder icon slots in the new
          shell; the legacy reachability constraint does not apply to app-next
          (04 §7) and the legacy app itself is frozen read-only. */}
      <div className="temp-controls">
        <Button variant="ghost" size="icon" className="iconbtn" title={t("settings")}>
          <Settings />
        </Button>
        <Button variant="ghost" size="icon" className="iconbtn" title={t("theme")}>
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
