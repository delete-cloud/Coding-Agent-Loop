"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";

import { CodexAccountsCard, type CodexClient } from "@/components/business/codex-accounts-card";
import { ModelCombobox } from "@/components/business/model-combobox";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import type { RuntimeConfigPatch } from "@/lib/connected-chat/client";
import type { ProviderModels, RuntimeConfigUpdate } from "@/lib/connected-chat/wire";
import {
  errorMessageOf,
  formatProviderAccountLabel,
  isCodexProvider,
  isTapeReboundError,
  listableProviders,
  persistSessionDefaults,
  resolveProviderAccount,
} from "@/lib/session-settings";

export interface SettingsClient extends CodexClient {
  listProviderModels(provider: string, signal?: AbortSignal): Promise<ProviderModels>;
  updateRuntimeConfig(
    sessionId: string,
    patch: RuntimeConfigPatch,
    signal?: AbortSignal,
  ): Promise<RuntimeConfigUpdate>;
}

export interface SettingsPanelProps {
  open: boolean;
  sessionId: string | null;
  providerName: string | null;
  modelName: string | null;
  client: SettingsClient;
  onApply: (patch: RuntimeConfigPatch) => Promise<void>;
}

export function SettingsPanel({
  open,
  sessionId,
  providerName,
  modelName,
  client,
  onApply,
}: SettingsPanelProps) {
  const t = useTranslations("settings");
  const [provider, setProvider] = useState(providerName ?? "anthropic");
  const [model, setModel] = useState(modelName ?? "");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [oauthAccounts, setOauthAccounts] = useState<Array<{ provider: string; label: string }>>([]);
  const oauthProviders = useMemo(
    () => oauthAccounts.map((account) => account.provider),
    [oauthAccounts],
  );
  const [liveModels, setLiveModels] = useState<string[]>([]);
  const [modelsStatus, setModelsStatus] = useState<"loading" | "ready">("loading");
  const [applying, setApplying] = useState(false);
  const [feedback, setFeedback] = useState<"saved" | "error" | "tapeRebound" | null>(null);
  const [errorText, setErrorText] = useState("");

  useEffect(() => {
    if (providerName) setProvider(resolveProviderAccount(providerName, oauthProviders));
    if (modelName) setModel(modelName);
  }, [providerName, modelName, oauthProviders]);

  useEffect(() => {
    let alive = true;
    client
      .listOAuthAccounts()
      .then((accounts) => {
        if (alive) setOauthAccounts(accounts.map((account) => ({
          provider: account.provider,
          label: account.label,
        })));
      })
      .catch(() => {
        if (alive) setOauthAccounts([]);
      });
    return () => {
      alive = false;
    };
  }, [client]);

  useEffect(() => {
    const nextProvider = provider.trim();
    if (!nextProvider) {
      setLiveModels([]);
      setModelsStatus("ready");
      return;
    }
    setModelsStatus("loading");
    const controller = new AbortController();
    const timer = setTimeout(() => {
      client
        .listProviderModels(nextProvider, controller.signal)
        .then((listed) => {
          setLiveModels(listed.source === "live" ? listed.models : []);
          setModelsStatus("ready");
        })
        .catch(() => {
          setLiveModels([]);
          setModelsStatus("ready");
        });
    }, 250);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [client, provider]);

  const providerOptions = useMemo(() => {
    const listed = listableProviders(oauthProviders);
    const current = resolveProviderAccount(provider, oauthProviders);
    return [...new Set([current, ...listed].filter((item) => item.trim()))];
  }, [provider, oauthProviders]);
  const hideApiKey = isCodexProvider(provider);

  if (!open) return null;

  const apply = async () => {
    const resolved = resolveProviderAccount(provider.trim(), oauthProviders);
    const patch: RuntimeConfigPatch = {
      provider: resolved,
      model: model.trim(),
    };
    if (baseUrl.trim()) patch.base_url = baseUrl.trim();
    if (!hideApiKey && apiKey.trim()) patch.api_key = apiKey.trim();
    const nextDefaults = {
      provider: resolved,
      model: patch.model ?? "",
      base_url: patch.base_url ?? "",
    };
    setApplying(true);
    setFeedback(null);
    setErrorText("");
    try {
      await onApply(patch);
      persistSessionDefaults(nextDefaults);
      setFeedback("saved");
    } catch (error) {
      if (isTapeReboundError(error)) {
        persistSessionDefaults(nextDefaults);
        setFeedback("tapeRebound");
      } else {
        setErrorText(errorMessageOf(error));
        setFeedback("error");
      }
    } finally {
      setApplying(false);
    }
  };

  const statusText =
    feedback === "saved"
      ? t("saved")
      : feedback === "tapeRebound"
        ? t("tapeRebound")
        : [t("saveFailed"), errorText].filter((item) => item.length > 0).join(" · ");

  return (
    <section className="settings-panel" aria-label={t("label")}>
      <div className="settings-head">
        <h3>{t("title")}</h3>
        {feedback ? (
          <span role="status" className={feedback === "saved" ? "settings-ok" : "settings-err"}>
            {statusText}
          </span>
        ) : null}
      </div>
      {sessionId ? null : <p className="panel-note">{t("noSession")}</p>}
      <label className="settings-field" htmlFor="settings-provider">
        {t("provider")}
        <Select
          id="settings-provider"
          value={provider}
          onChange={(event) => setProvider(event.target.value)}
        >
          {providerOptions.map((item) => (
            <option key={item} value={item}>
              {formatProviderAccountLabel(item, oauthAccounts)}
            </option>
          ))}
        </Select>
      </label>
      <label className="settings-field" htmlFor="settings-model">
        {t("model")}
        <ModelCombobox
          id="settings-model"
          value={model}
          onChange={setModel}
          models={liveModels}
          status={modelsStatus}
        />
      </label>
      {hideApiKey ? (
        <p className="panel-note">{t("codexUsesOAuth")}</p>
      ) : (
        <div className="settings-field">
          <label htmlFor="settings-api-key">{t("apiKey")}</label>
          <Input
            id="settings-api-key"
            type="password"
            autoComplete="off"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
          />
          <span className="panel-note">{t("apiKeyHint")}</span>
        </div>
      )}
      <label className="settings-field" htmlFor="settings-base-url">
        {t("baseUrl")}
        <Input
          id="settings-base-url"
          value={baseUrl}
          onChange={(event) => setBaseUrl(event.target.value)}
        />
      </label>
      <Button disabled={applying || !provider.trim() || !model.trim()} onClick={() => void apply()}>
        {applying ? t("applying") : t("apply")}
      </Button>
      <CodexAccountsCard client={client} />
    </section>
  );
}
