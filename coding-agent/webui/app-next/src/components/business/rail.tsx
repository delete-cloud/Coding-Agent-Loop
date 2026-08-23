"use client";

import { MessagesSquare, Settings } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

/**
 * Rail — 40px vertical icon column at the far left of the AppFrame (02 §5).
 * Pure structural/visual placeholder in Slice 1: the slots have no click
 * behavior and the health dot is a neutral static placeholder (--fg-faint),
 * NOT wired to any connection state (03 #1/#7 — real status lands in Slice 2).
 */
export function Rail() {
  const t = useTranslations("rail");

  return (
    <nav className="rail" aria-label={t("navLabel")}>
      <Button
        variant="ghost"
        size="icon"
        className="rail-btn active"
        title={t("sessions")}
        aria-current="page"
      >
        <MessagesSquare />
      </Button>
      <Button variant="ghost" size="icon" className="rail-btn" title={t("settings")}>
        <Settings />
      </Button>
      <div className="spacer" />
      <span className="rail-dot" role="img" aria-label={t("health")} />
    </nav>
  );
}
