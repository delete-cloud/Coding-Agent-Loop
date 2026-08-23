// @vitest-environment node
import { useTranslations } from "next-intl";
import { renderToString } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import RootLayout from "./layout";

// The ENVIRONMENT_FALLBACK warning only fires on the SERVER pass of
// useTranslations (use-intl gates it on `typeof window === 'undefined'`),
// which is exactly the Next dev/build prerender that flagged the P3 finding.
// A jsdom render cannot reproduce it, so this test renders to string in a
// node environment. Minimal consumer matching the reported failure site
// (AppFrame useTranslations).
function TranslatedChild() {
  const t = useTranslations("sessionbar");
  return <span data-testid="translated">{t("detailsToggle")}</span>;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("RootLayout i18n provider", () => {
  it("configures an explicit timeZone, so SSR emits no ENVIRONMENT_FALLBACK", () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    const html = renderToString(
      <RootLayout>
        <TranslatedChild />
      </RootLayout>,
    );
    // Sanity: messages resolved and the translated child actually rendered.
    expect(html).toMatch(/<span data-testid="translated">[^<]+<\/span>/);

    const fallbackWarnings = errorSpy.mock.calls.filter((args) =>
      args.some((arg) => String(arg).includes("ENVIRONMENT_FALLBACK")),
    );
    expect(fallbackWarnings).toEqual([]);
  });
});
