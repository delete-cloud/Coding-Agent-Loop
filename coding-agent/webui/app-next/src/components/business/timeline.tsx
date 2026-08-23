"use client";

import { useTranslations } from "next-intl";

/**
 * Timeline — static placeholder messages for Slice 1 (04 §7: zero data
 * fetching, all copy via i18n keys with string-literal arguments only).
 * Shares the 824/780 content column with the composer (02 §8).
 */
export function Timeline() {
  const t = useTranslations("timeline");

  const messages = [
    { role: t("userRole"), body: t("m1.body"), meta: t("m1.meta") },
    { role: t("assistantRole"), body: t("m2.body"), meta: t("m2.meta") },
    { role: t("userRole"), body: t("m3.body"), meta: t("m3.meta") },
    { role: t("assistantRole"), body: t("m4.body"), meta: t("m4.meta") },
  ];

  return (
    <div className="timeline">
      {messages.map((message, index) => (
        <section key={index}>
          <div className="role">{message.role}</div>
          <div className="msg">
            <p>{message.body}</p>
            <div className="msg-meta">{message.meta}</div>
          </div>
        </section>
      ))}
    </div>
  );
}
