import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";

import zhMessages from "../../../messages/zh.json";
import { ChatApiError } from "@/lib/connected-chat/client";
import { CodexAccountsCard } from "@/components/business/codex-accounts-card";

function notFound() {
  return new ChatApiError(404, {
    code: "not_found",
    message: "not found",
    retryable: false,
  });
}

describe("CodexAccountsCard 404 degrade", () => {
  it("degrades start and list 404s to an unsupported note", async () => {
    render(
      <NextIntlClientProvider locale="zh" messages={zhMessages}>
        <CodexAccountsCard
          client={{
            listOAuthAccounts: vi.fn(async () => {
              throw notFound();
            }),
            listCodexFlows: vi.fn(async () => {
              throw notFound();
            }),
            startCodexOAuth: vi.fn(async () => {
              throw notFound();
            }),
            getCodexFlow: vi.fn(),
            cancelCodexFlow: vi.fn(),
            deleteOAuthAccount: vi.fn(),
          }}
        />
      </NextIntlClientProvider>,
    );

    expect(await screen.findByText(zhMessages.codex.unsupported)).toBeTruthy();
  });
});
