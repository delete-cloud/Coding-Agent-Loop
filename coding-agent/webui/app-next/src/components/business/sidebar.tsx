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

interface SidebarBaseProps {
  sessions: SessionItem[];
  selectedId: string;
  onSelect: (id: string) => void;
  /** Create-session action. Optional: the Slice 1 shell renders the button
   *  inert; the connected catalog always passes it. */
  onCreateSession?: () => void;
  createPending?: boolean;
  createError?: string;
}

/**
 * Catalog state is a discriminated union so the error affordance is
 * type-checked: `status: "error"` is only expressible together with the
 * retry action that recovers from it.
 */
export type SidebarProps = SidebarBaseProps &
  (
    | { status?: "loading" | "ready"; onRetry?: () => void }
    | { status: "error"; onRetry: () => void }
  );

/** Muted in-list note; Tailwind semantic tokens only (01 §6 / 04 §3). */
const LIST_NOTE_CLASS = "px-2.5 py-2 text-[11px] text-muted-foreground";

/**
 * Sidebar — 240px session list column (02 §6).
 * Default props preserve the Slice 1 static shell exactly; the connected
 * catalog drives `status` (loading/ready/error) and the create action.
 * The list keeps `.session-list` as scroll region ① (02 §2): state notes
 * render INSIDE it, never as a separate scroll container.
 */
export function Sidebar({
  sessions,
  selectedId,
  onSelect,
  status = "ready",
  onRetry,
  onCreateSession,
  createPending = false,
  createError = "",
}: SidebarProps) {
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
        {status === "loading" ? (
          <p className={LIST_NOTE_CLASS}>{t("loading")}</p>
        ) : (
          <>
            {status === "error" && (
              <div className={LIST_NOTE_CLASS} role="alert">
                <p>{t("error")}</p>
                <Button
                  variant="ghost"
                  className="h-auto px-0 py-1 text-[11px] text-foreground"
                  onClick={onRetry}
                >
                  {t("retry")}
                </Button>
              </div>
            )}
            {status === "ready" && sessions.length === 0 && (
              <p className={LIST_NOTE_CLASS}>{t("empty")}</p>
            )}
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
          </>
        )}
      </div>
      {createError.length > 0 && (
        <div className={LIST_NOTE_CLASS} role="alert">
          <p>{t("createFailed")}</p>
          <p>{createError}</p>
        </div>
      )}
      <Button
        variant="ghost"
        className="new-session"
        onClick={onCreateSession}
        disabled={createPending}
      >
        {createPending ? t("creating") : t("newSession")}
      </Button>
    </aside>
  );
}
