"use client";

import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export interface SessionItem {
  id: string;
  title: string;
  meta: string;
}

interface SidebarProps {
  sessions: SessionItem[];
  selectedId: string;
  onSelect: (id: string) => void;
}

/**
 * Sidebar — 240px session list column (02 §6).
 * Slice 1 renders static placeholder rows only; selecting a row switches the
 * `?session=` search param (04 §7) which also closes the details pane (02 §4.2).
 */
export function Sidebar({ sessions, selectedId, onSelect }: SidebarProps) {
  const t = useTranslations("sidebar");

  return (
    <aside className="sidebar" aria-label={t("label")}>
      <div className="search-slot">
        <Input
          className="search"
          placeholder={t("searchPlaceholder")}
          aria-label={t("searchLabel")}
        />
      </div>
      <div className="session-list">
        {sessions.map((session) => (
          <Button
            key={session.id}
            variant="ghost"
            className={cn("session", session.id === selectedId && "sel")}
            aria-current={session.id === selectedId ? "page" : undefined}
            onClick={() => onSelect(session.id)}
          >
            <span className="session-t">{session.title}</span>
            <span className="session-m">{session.meta}</span>
          </Button>
        ))}
      </div>
      <Button variant="ghost" className="new-session">
        {t("newSession")}
      </Button>
    </aside>
  );
}
