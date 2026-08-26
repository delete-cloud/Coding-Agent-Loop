"use client";

import { MessagesSquare, Settings } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import type { RailHealth } from "@/hooks/use-connected-chat";

/**
 * Rail — 40px vertical icon column at the far left of the AppFrame (02 §5).
 * The icon slots stay inert placeholders. The health dot reflects transport
 * health through the typed `health` prop (rendered as `data-health`); with no
 * prop it stays the neutral static placeholder (--fg-faint), and real status
 * colors remain a Slice 2 concern (03 #1/#7 — CSS is untouched).
 */
export function Rail({ health }: { health?: RailHealth }) {
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
      <span className="rail-dot" role="img" aria-label={t("health")} data-health={health} />
    </nav>
  );
}
