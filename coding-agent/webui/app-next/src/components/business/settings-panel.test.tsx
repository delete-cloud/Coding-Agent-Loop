import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, describe, expect, it, vi } from "vitest";

import zhMessages from "../../../messages/zh.json";
import { SETTINGS_LS_KEY } from "@/lib/session-settings";
import { ChatApiError } from "@/lib/connected-chat/client";
import { SettingsPanel } from "@/components/business/settings-panel";

function renderSettings(
  props: Partial<Parameters<typeof SettingsPanel>[0]> = {},
) {
  const onApply = props.onApply ?? vi.fn(async () => {});
  const result = render(
    <NextIntlClientProvider locale="zh" messages={zhMessages}>
      <SettingsPanel
        open
        sessionId="session-01"
        providerName="anthropic"
        modelName="claude-sonnet-4"
        client={{
          listProviderModels: vi.fn(async () => ({
            provider: "anthropic",
            source: "unavailable" as const,
            models: [],
          })),
          listOAuthAccounts: vi.fn(async () => {
            throw Object.assign(new Error("HTTP 404: not_found"), { status: 404 });
          }),
          listCodexFlows: vi.fn(async () => {
            throw Object.assign(new Error("HTTP 404: not_found"), { status: 404 });
          }),
          startCodexOAuth: vi.fn(),
          getCodexFlow: vi.fn(),
          cancelCodexFlow: vi.fn(),
          deleteOAuthAccount: vi.fn(),
          updateRuntimeConfig: vi.fn(),
        }}
        {...props}
        onApply={onApply}
      />
    </NextIntlClientProvider>,
  );
  return { ...result, onApply };
}

afterEach(() => {
  localStorage.clear();
});

describe("SettingsPanel apply", () => {
  it("applies provider, model, optional base_url and api_key", async () => {
    const { onApply } = renderSettings();

    fireEvent.change(screen.getByLabelText(zhMessages.settings.provider), {
      target: { value: "deepseek" },
    });
    fireEvent.change(screen.getByLabelText(zhMessages.settings.model), {
      target: { value: "deepseek-chat" },
    });
    fireEvent.change(screen.getByLabelText(zhMessages.settings.apiKey), {
      target: { value: "sk-secret" },
    });
    fireEvent.change(screen.getByLabelText(zhMessages.settings.baseUrl), {
      target: { value: "https://api.deepseek.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: zhMessages.settings.apply }));

    await waitFor(() => {
      expect(onApply).toHaveBeenCalledWith({
        provider: "deepseek",
        model: "deepseek-chat",
        base_url: "https://api.deepseek.com",
        api_key: "sk-secret",
      });
    });
  });

  it("never writes the pasted api_key to localStorage", async () => {
    renderSettings();

    fireEvent.change(screen.getByLabelText(zhMessages.settings.apiKey), {
      target: { value: "sk-must-not-persist" },
    });
    fireEvent.click(screen.getByRole("button", { name: zhMessages.settings.apply }));

    await waitFor(() => {
      expect(screen.getByRole("status").textContent).toContain(zhMessages.settings.saved);
    });

    expect(localStorage.getItem(SETTINGS_LS_KEY) ?? "").not.toContain("sk-must-not-persist");
    expect(JSON.stringify(localStorage)).not.toContain("sk-must-not-persist");
  });

  it("does not persist session defaults when Apply fails", async () => {
    const previousDefaults = JSON.stringify({
      provider: "anthropic",
      model: "claude-sonnet-4",
      base_url: "",
    });
    localStorage.setItem(SETTINGS_LS_KEY, previousDefaults);
    renderSettings({
      onApply: vi.fn(async () => {
        throw new Error("runtime PATCH failed");
      }),
    });

    fireEvent.change(screen.getByLabelText(zhMessages.settings.provider), {
      target: { value: "deepseek" },
    });
    fireEvent.change(screen.getByLabelText(zhMessages.settings.model), {
      target: { value: "deepseek-chat" },
    });
    fireEvent.click(screen.getByRole("button", { name: zhMessages.settings.apply }));

    await waitFor(() => {
      expect(screen.getByRole("status").textContent).toContain(
        zhMessages.settings.saveFailed,
      );
    });
    expect(localStorage.getItem(SETTINGS_LS_KEY)).toBe(previousDefaults);
  });

  it("shows the server error text when Apply fails", async () => {
    renderSettings({
      onApply: vi.fn(async () => {
        throw new ChatApiError(500, {
          code: "provider_unavailable",
          message: "No provider configured",
          retryable: false,
        });
      }),
    });

    fireEvent.click(screen.getByRole("button", { name: zhMessages.settings.apply }));

    await waitFor(() => {
      const status = screen.getByRole("status").textContent ?? "";
      expect(status).toContain("No provider configured");
      expect(status).toContain(zhMessages.settings.saveFailed);
    });
  });

  it("persists next-session defaults when the tape cannot be rebound", async () => {
    renderSettings({
      onApply: vi.fn(async () => {
        throw new ChatApiError(500, {
          code: "http_error",
          message: "session tape target cannot be rebound",
          retryable: false,
        });
      }),
    });

    fireEvent.change(screen.getByLabelText(zhMessages.settings.provider), {
      target: { value: "deepseek" },
    });
    fireEvent.change(screen.getByLabelText(zhMessages.settings.model), {
      target: { value: "deepseek-chat" },
    });
    fireEvent.click(screen.getByRole("button", { name: zhMessages.settings.apply }));

    await waitFor(() => {
      const status = screen.getByRole("status").textContent ?? "";
      expect(status).toContain(zhMessages.settings.tapeRebound);
      expect(status.toLowerCase()).not.toContain("tape");
      expect(status).not.toContain("磁带");
    });
    expect(JSON.parse(localStorage.getItem(SETTINGS_LS_KEY) ?? "{}")).toEqual({
      provider: "deepseek",
      model: "deepseek-chat",
      base_url: "",
    });
  });
});

describe("SettingsPanel providers", () => {
  it("lists connected codex:<label> keys and hides bare codex", async () => {
    renderSettings({
      client: {
        listProviderModels: vi.fn(async () => ({
          provider: "anthropic",
          source: "unavailable" as const,
          models: [],
        })),
        listOAuthAccounts: vi.fn(async () => [
          { provider: "codex:kina0630test-gmail-com", label: "kina" },
        ]),
        listCodexFlows: vi.fn(async () => []),
        startCodexOAuth: vi.fn(),
        getCodexFlow: vi.fn(),
        cancelCodexFlow: vi.fn(),
        deleteOAuthAccount: vi.fn(),
        updateRuntimeConfig: vi.fn(),
      },
    });

    await waitFor(() => {
      const select = screen.getByLabelText(zhMessages.settings.provider) as HTMLSelectElement;
      const values = [...select.options].map((option) => option.value);
      expect(values).toContain("codex:kina0630test-gmail-com");
      expect(values).not.toContain("codex");
      const labeled = [...select.options].find((option) => option.value === "codex:kina0630test-gmail-com");
      expect(labeled?.textContent).toBe("Codex · kina");
    });
  });

  it("applies the connected labeled Codex account instead of bare codex", async () => {
    const { onApply } = renderSettings({
      providerName: "codex",
      modelName: "gpt-5.4",
      client: {
        listProviderModels: vi.fn(async () => ({
          provider: "codex:kina0630test-gmail-com",
          source: "unavailable" as const,
          models: [],
        })),
        listOAuthAccounts: vi.fn(async () => [
          { provider: "codex:kina0630test-gmail-com", label: "kina" },
        ]),
        listCodexFlows: vi.fn(async () => []),
        startCodexOAuth: vi.fn(),
        getCodexFlow: vi.fn(),
        cancelCodexFlow: vi.fn(),
        deleteOAuthAccount: vi.fn(),
        updateRuntimeConfig: vi.fn(),
      },
    });

    await waitFor(() => {
      expect(
        (screen.getByLabelText(zhMessages.settings.provider) as HTMLSelectElement).value,
      ).toBe("codex:kina0630test-gmail-com");
    });
    fireEvent.click(screen.getByRole("button", { name: zhMessages.settings.apply }));

    await waitFor(() => {
      expect(onApply).toHaveBeenCalledWith({
        provider: "codex:kina0630test-gmail-com",
        model: "gpt-5.4",
      });
    });
  });
});

describe("SettingsPanel remote model combobox", () => {
  it("opens the full remote list on focus and filters as the user types", async () => {
    renderSettings({
      client: {
        listProviderModels: vi.fn(async () => ({
          provider: "anthropic",
          source: "live" as const,
          models: ["claude-sonnet-4", "claude-opus-4"],
        })),
        listOAuthAccounts: vi.fn(async () => []),
        listCodexFlows: vi.fn(async () => []),
        startCodexOAuth: vi.fn(),
        getCodexFlow: vi.fn(),
        cancelCodexFlow: vi.fn(),
        deleteOAuthAccount: vi.fn(),
        updateRuntimeConfig: vi.fn(),
      },
    });

    const input = screen.getByLabelText(zhMessages.settings.model);
    await waitFor(() => expect(input.getAttribute("role")).toBe("combobox"));
    fireEvent.focus(input);
    await waitFor(() => {
      expect(screen.getByRole("option", { name: "claude-sonnet-4" })).toBeDefined();
      expect(screen.getByRole("option", { name: "claude-opus-4" })).toBeDefined();
    });

    fireEvent.change(input, { target: { value: "opus" } });
    expect(screen.queryByRole("option", { name: "claude-sonnet-4" })).toBeNull();
    expect(screen.getByRole("option", { name: "claude-opus-4" })).toBeDefined();
  });

  it("shows loading and empty remote model states", async () => {
    const { promise, resolve } = Promise.withResolvers<{
      provider: string;
      source: "live";
      models: string[];
    }>();
    renderSettings({
      client: {
        listProviderModels: vi.fn(() => promise),
        listOAuthAccounts: vi.fn(async () => []),
        listCodexFlows: vi.fn(async () => []),
        startCodexOAuth: vi.fn(),
        getCodexFlow: vi.fn(),
        cancelCodexFlow: vi.fn(),
        deleteOAuthAccount: vi.fn(),
        updateRuntimeConfig: vi.fn(),
      },
    });

    fireEvent.focus(screen.getByLabelText(zhMessages.settings.model));
    await waitFor(() => {
      expect(screen.getByText(zhMessages.settings.modelsLoading)).toBeDefined();
    });

    resolve({ provider: "anthropic", source: "live", models: [] });
    await waitFor(() => {
      expect(screen.getByText(zhMessages.settings.modelsEmpty)).toBeDefined();
    });
  });
});
