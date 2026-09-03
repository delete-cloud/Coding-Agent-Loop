"use client";

import { ChevronDown } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type RefObject } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  formatProviderAccountLabel,
  isCodexProvider,
  listableProviders,
  providerKind,
  resolveProviderAccount,
} from "@/lib/session-settings";

export type ModelListStatus = "loading" | "ready";

export type ProviderAccount = {
  provider: string;
  label: string;
};

function usePopupDismiss(
  open: boolean,
  rootRef: RefObject<HTMLElement | null>,
  setOpen: (open: boolean) => void,
  escape = false,
) {
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    if (!escape) {
      return () => document.removeEventListener("pointerdown", onPointerDown);
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, rootRef, setOpen, escape]);
}

export function ModelCombobox({
  id,
  value,
  onChange,
  onCommit,
  models,
  status,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  onCommit?: (value: string) => void;
  models: readonly string[];
  status: ModelListStatus;
}) {
  const t = useTranslations("settings");
  const listId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState(false);
  const query = value.trim().toLowerCase();
  const filtered = typed
    ? models.filter((item) => item.toLowerCase().includes(query))
    : [...models];

  usePopupDismiss(open, rootRef, setOpen);

  const showList = () => {
    setTyped(false);
    setOpen(true);
  };

  return (
    <div className="model-combobox" ref={rootRef}>
      <Input
        id={id}
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        value={value}
        autoComplete="off"
        onChange={(event) => {
          setTyped(true);
          setOpen(true);
          onChange(event.target.value);
        }}
        onClick={showList}
        onFocus={showList}
        onBlur={() => {
          onCommit?.(value);
        }}
      />
      {open ? (
        <ul id={listId} role="listbox" className="model-combobox-list">
          {status === "loading" ? (
            <li className="model-combobox-note">{t("modelsLoading")}</li>
          ) : filtered.length === 0 ? (
            <li className="model-combobox-note">{t("modelsEmpty")}</li>
          ) : (
            filtered.map((item) => (
              <li
                key={item}
                role="option"
                aria-selected={item === value}
                onMouseDown={(event) => {
                  event.preventDefault();
                  onChange(item);
                  onCommit?.(item);
                  setTyped(false);
                  setOpen(false);
                }}
              >
                {item}
              </li>
            ))
          )}
        </ul>
      ) : null}
    </div>
  );
}

export function ModelPicker({
  provider,
  model,
  oauthProviders,
  accounts,
  models,
  status,
  onProviderChange,
  onModelChange,
}: {
  provider: string;
  model: string;
  oauthProviders: readonly string[];
  accounts: readonly ProviderAccount[];
  models: readonly string[];
  status: ModelListStatus;
  onProviderChange: (provider: string) => void;
  onModelChange: (model: string) => void;
}) {
  const t = useTranslations("composer");
  const tSettings = useTranslations("settings");
  const listId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const resolved = resolveProviderAccount(provider, oauthProviders);
  const kind = providerKind(resolved);

  usePopupDismiss(open, rootRef, setOpen, true);

  const providers = useMemo(() => {
    const listed = listableProviders();
    return [...new Set([kind, ...listed].filter((item) => item.trim()))];
  }, [kind]);
  const accountOptions = accounts.filter((account) => isCodexProvider(account.provider));

  const providerLabel = formatProviderAccountLabel(resolved, accounts);
  const chipName = [model.trim(), providerLabel.trim()].filter((part) => part.length > 0).join(" · ");

  return (
    <div className="model-picker" ref={rootRef}>
      <Button
        type="button"
        variant="ghost"
        className="model-picker-trigger"
        aria-label={chipName.length > 0 ? chipName : t("model")}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        onClick={() => setOpen((value) => !value)}
      >
        {model.trim() ? <span className="model-picker-model">{model}</span> : null}
        {providerLabel.trim() ? (
          <span className="model-picker-provider-label">{providerLabel}</span>
        ) : null}
        <ChevronDown className="model-picker-chevron" />
      </Button>
      {open ? (
        <div className="model-picker-menu" id={listId}>
          <div className="model-picker-providers" role="listbox" aria-label={t("provider")}>
            {providers.map((item) => (
              <Button
                key={item}
                type="button"
                variant="ghost"
                role="option"
                aria-selected={item === kind}
                className="model-picker-provider"
                onClick={() => {
                  const next = item === "codex" ? resolveProviderAccount("codex", oauthProviders) : item;
                  if (next !== provider) onProviderChange(next);
                }}
              >
                {formatProviderAccountLabel(item, [])}
              </Button>
            ))}
            {kind === "codex" && accountOptions.length > 0 ? (
              <div className="model-picker-accounts" role="listbox" aria-label={t("account")}>
                {accountOptions.map((account) => (
                  <Button
                    key={account.provider}
                    type="button"
                    variant="ghost"
                    role="option"
                    aria-selected={account.provider === resolved}
                    className="model-picker-provider"
                    onClick={() => {
                      if (account.provider !== provider) onProviderChange(account.provider);
                    }}
                  >
                    {account.label.trim() || account.provider}
                  </Button>
                ))}
              </div>
            ) : null}
          </div>
          <ul className="model-picker-models" role="listbox" aria-label={t("model")}>
            {status === "loading" ? (
              <li className="model-combobox-note">{tSettings("modelsLoading")}</li>
            ) : models.length === 0 ? (
              <li className="model-combobox-note">{tSettings("modelsEmpty")}</li>
            ) : (
              models.map((item) => (
                <li key={item} role="presentation">
                  <Button
                    type="button"
                    variant="ghost"
                    role="option"
                    aria-selected={item === model}
                    className="model-picker-model-option"
                    onClick={() => {
                      onModelChange(item);
                      setOpen(false);
                    }}
                  >
                    {item}
                  </Button>
                </li>
              ))
            )}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
