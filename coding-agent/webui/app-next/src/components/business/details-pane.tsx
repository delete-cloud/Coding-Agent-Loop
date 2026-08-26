"use client";

import { useTranslations } from "next-intl";
import type { RefObject } from "react";

import { cn } from "@/lib/utils";

interface DetailsPaneProps {
  open: boolean;
  paneRef: RefObject<HTMLElement | null>;
  provider?: string | null;
  model?: string | null;
}

function SetRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="set-row">
      <span className="set-row-k">{label}</span>
      <span className="set-row-v">{value}</span>
    </div>
  );
}

/**
 * Details pane — 360px, closable (02 §4).
 * Closed = collapsed, never unmounted: the .closed class applies
 * width/visibility/pointer-events (not display:none), `inert` + aria-hidden
 * keep the closed subtree out of the Tab order while preserving its state.
 * `.details-scroll` is the ONLY scroll container inside (02 §2).
 */
export function DetailsPane({ open, paneRef, provider, model }: DetailsPaneProps) {
  const t = useTranslations("details");

  return (
    <aside
      id="details-pane"
      ref={paneRef}
      className={cn("details", !open && "closed")}
      aria-hidden={!open}
      inert={!open}
      aria-label={t("label")}
    >
      <div className="details-scroll">
        <h4 className="details-h4">{t("sessionSection")}</h4>
        <div className="details-rows">
          <SetRow
            label={t("providerKey")}
            value={provider && provider.trim().length > 0 ? provider : t("unset")}
          />
          <SetRow
            label={t("modelKey")}
            value={model && model.trim().length > 0 ? model : t("unset")}
          />
          <SetRow label={t("contextKey")} value={t("contextValue")} />
          <SetRow label={t("tokensKey")} value={t("tokensValue")} />
        </div>
        <h4 className="details-h4">{t("workspaceSection")}</h4>
        <div className="details-rows">
          <SetRow label={t("branchKey")} value={t("branchValue")} />
          <SetRow label={t("changedKey")} value={t("changedValue")} />
        </div>
        <p className="panel-note">{t("note")}</p>
      </div>
    </aside>
  );
}
