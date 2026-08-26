import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

  it("notifies the parent after disconnect refreshes the account list", async () => {
    const onAccountsChange = vi.fn();
    const remaining = [{ provider: "codex:kept", label: "kept" }];
    const listOAuthAccounts = vi
      .fn()
      .mockResolvedValueOnce([{ provider: "codex:gone", label: "gone" }])
      .mockResolvedValueOnce(remaining);
    const deleteOAuthAccount = vi.fn(async () => {});
    vi.stubGlobal("confirm", () => true);

    render(
      <NextIntlClientProvider locale="zh" messages={zhMessages}>
        <CodexAccountsCard
          client={{
            listOAuthAccounts,
            listCodexFlows: vi.fn(async () => []),
            startCodexOAuth: vi.fn(),
            getCodexFlow: vi.fn(),
            cancelCodexFlow: vi.fn(),
            deleteOAuthAccount,
          }}
          onAccountsChange={onAccountsChange}
        />
      </NextIntlClientProvider>,
    );

    expect(await screen.findByText("gone")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: zhMessages.codex.disconnect }));

    await waitFor(() => {
      expect(deleteOAuthAccount).toHaveBeenCalledWith("codex:gone");
      expect(onAccountsChange).toHaveBeenLastCalledWith(remaining);
    });
  });
});
