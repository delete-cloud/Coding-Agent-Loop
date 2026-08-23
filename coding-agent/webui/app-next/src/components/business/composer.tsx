"use client";

import { useTranslations } from "next-intl";

/**
 * Composer — static placeholder frame for Slice 1 (04 §7: the real composer
 * wiring is deferred to Slice 2). Shares the 824/780 content column with the
 * timeline (02 §8); frame consumes --hairline-2 + --bg-raise.
 */
export function Composer() {
  const t = useTranslations("composer");

  return <div className="composer">{t("placeholder")}</div>;
}
