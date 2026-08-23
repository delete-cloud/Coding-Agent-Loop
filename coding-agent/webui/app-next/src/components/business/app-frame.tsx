"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";

import { Composer } from "@/components/business/composer";
import { DetailsPane } from "@/components/business/details-pane";
import { Rail } from "@/components/business/rail";
import { SessionBar } from "@/components/business/session-bar";
import { Sidebar, type SessionItem } from "@/components/business/sidebar";
import { Timeline } from "@/components/business/timeline";

/** Mid-tier band where the details pane becomes an overlay (02 §4.2).
 *  Dual-bounded on purpose: <768px is out of scope and must stay untouched. */
const MID_TIER_QUERY = "(min-width: 768px) and (max-width: 1143px)";

/**
 * AppFrame — the Slice 1 physical shell (02 §2).
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
export function AppFrame() {
  const tSessions = useTranslations("sidebar.sessions");

  // Static placeholder sessions (04 §7). Keys are string literals — computed
  // i18n keys are forbidden by the 04 §4 contract.
  const sessions: SessionItem[] = [
    { id: "s1", title: tSessions("s1.title"), meta: tSessions("s1.meta") },
    { id: "s2", title: tSessions("s2.title"), meta: tSessions("s2.meta") },
    { id: "s3", title: tSessions("s3.title"), meta: tSessions("s3.meta") },
    { id: "s4", title: tSessions("s4.title"), meta: tSessions("s4.meta") },
  ];

  const searchParams = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();

  const sessionParam = searchParams.get("session");
  const selectedId = sessions.some((s) => s.id === sessionParam) ? (sessionParam as string) : "s1";
  const selected = sessions.find((s) => s.id === selectedId) ?? sessions[0];

  const [detailsOpen, setDetailsOpen] = useState(false);
  const openRef = useRef(detailsOpen);
  openRef.current = detailsOpen;

  const timelineScrollRef = useRef<HTMLDivElement>(null);
  const composerSlotRef = useRef<HTMLDivElement>(null);
  const detailsRef = useRef<HTMLElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);

  // 02 §4.2: switching sessions unconditionally returns details to closed.
  // This must fire on EVERY raw ?session= change — including s1 → absent and
  // s1 → invalid, which both normalize back to selectedId="s1" — so the
  // dependency is the raw sessionParam, never the normalized selectedId.
  useEffect(() => {
    setDetailsOpen(false);
  }, [sessionParam]);

  // 02 §4.2 click-away: listeners on exactly the two work surfaces; they
  // no-op unless the overlay is open AND the mid-tier media query matches.
  // No stopPropagation anywhere; click-away never moves focus.
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

  // 02 §4.5: Esc closes only while focus is inside the open pane, then
  // returns focus to the SessionBar toggle button.
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

  const selectSession = (id: string) => {
    if (id === selectedId) return;
    const params = new URLSearchParams(searchParams.toString());
    params.set("session", id);
    router.push(`${pathname}?${params.toString()}`);
  };

  return (
    <div className="appframe">
      <Rail />
      <Sidebar sessions={sessions} selectedId={selectedId} onSelect={selectSession} />
      <main className="conversation">
        <SessionBar
          title={selected.title}
          detailsOpen={detailsOpen}
          onToggleDetails={() => setDetailsOpen((open) => !open)}
          toggleRef={toggleRef}
        />
        <div className="timeline-scroll" ref={timelineScrollRef}>
          <Timeline />
        </div>
        <div className="composer-slot" ref={composerSlotRef}>
          <Composer />
        </div>
      </main>
      <DetailsPane open={detailsOpen} paneRef={detailsRef} />
    </div>
  );
}
