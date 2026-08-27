"use client";

import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";

import messages from "../../messages/zh.json";

/**
 * Client i18n boundary for the server root layout. next-intl runs WITHOUT
 * i18n routing — no [locale] segment, locale never enters the URL. zh is
 * the default locale (04 §4); messages are static JSON.
 */
export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <NextIntlClientProvider
      locale="zh"
      messages={messages}
      // Explicit stable IANA zone (project context is UTC+8): without it,
      // next-intl warns ENVIRONMENT_FALLBACK and sniffs the ambient zone,
      // which is nondeterministic for a static export.
      timeZone="Asia/Shanghai"
    >
      {children}
    </NextIntlClientProvider>
  );
}
