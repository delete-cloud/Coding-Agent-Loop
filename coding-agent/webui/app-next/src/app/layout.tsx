import type { Metadata } from "next";
import type { ReactNode } from "react";

import { AppProviders } from "./providers";

import "./globals.css";

/**
 * Root layout (04 §1/§4): a Server Component so Next App Router metadata
 * is serialized into the static export. The whole app below this file is
 * still client components; next-intl stays inside AppProviders.
 */
export const metadata: Metadata = {
  title: "CAL Night Console",
  applicationName: "CAL Night Console",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <body>
        <script
          dangerouslySetInnerHTML={{
            __html:
              'try{var t=localStorage.getItem("coding-agent-webui-theme");document.documentElement.setAttribute("data-theme",t==="light"?"light":"dark")}catch(e){document.documentElement.setAttribute("data-theme","dark")}',
          }}
        />
        <AppProviders>{children}</AppProviders>
      </body>
    </html>
  );
}
