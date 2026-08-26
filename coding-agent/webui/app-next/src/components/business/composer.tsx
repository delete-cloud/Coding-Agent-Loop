"use client";

import { useTranslations } from "next-intl";
import type { KeyboardEvent } from "react";

import { ModelPicker, type ProviderAccount } from "@/components/business/model-combobox";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { ConnectedChatStatus } from "@/lib/connected-chat/controller";

/**
 * Static Slice 1 shell: rendered when the composer is not yet wired to a
 * ConnectedChatView (04 §7). Kept byte-identical to the original placeholder.
 */
export interface ComposerStaticProps {
  draft?: undefined;
}

/**
 * Dynamic composer, wired 1:1 to ConnectedChatView fields/actions:
 *   draft        ← state.draft          onDraftChange ← setDraft
 *   status       ← state.status         onSend        ← send
 *   canResume    ← canResume            onCancel      ← cancel
 *   busy         = derived from status  onResume      ← resume
 *                                       onReload      ← reload
 * `busy` is derived (status === "sending" | "cancelling") exactly as the hook
 * derives ConnectedChatView.busy, so the view passes status only.
 */
export interface ComposerDynamicProps {
  draft: string;
  onDraftChange: (draft: string) => void;
  onSend: () => void;
  onCancel: () => void;
  onResume: () => void;
  onReload: () => void;
  status: ConnectedChatStatus;
  canResume: boolean;
  model?: string;
  onModelChange?: (model: string) => void;
  provider?: string;
  onProviderChange?: (provider: string) => void;
  oauthProviders?: readonly string[];
  accounts?: readonly ProviderAccount[];
  modelOptions?: readonly string[];
  modelStatus?: "loading" | "ready";
}

export type ComposerProps = ComposerStaticProps | ComposerDynamicProps;

/**
 * Composer — dynamic prompt input inside the existing `.composer` shell
 * (the 824/780 content column lives on `.composer-slot` in AppFrame and is
 * untouched). One shell, no new scroll surface. Enter sends, Shift+Enter
 * inserts a newline, and Enter during an active IME composition never sends.
 */
export function Composer(props: ComposerProps) {
  const t = useTranslations("composer");

  if (props.draft === undefined) {
    return <div className="composer">{t("placeholder")}</div>;
  }

  const {
    draft,
    onDraftChange,
    onSend,
    onCancel,
    onResume,
    onReload,
    status,
    canResume,
    model,
    onModelChange,
    provider,
    onProviderChange,
    oauthProviders,
    accounts,
    modelOptions,
    modelStatus,
  } = props;

  // Same derivation as ConnectedChatView.busy.
  const busy = status === "sending" || status === "cancelling";
  const replayRequired = status === "replay_required";
  const sendDisabled = draft.trim().length === 0 || busy || replayRequired;

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    if (!sendDisabled) onSend();
  };

  return (
    <div className="composer">
      {status === "error" && <p role="alert">{t("error")}</p>}
      {status === "reconnecting" && <p>{t("reconnecting")}</p>}
      {status === "replay_required" && <p>{t("replayRequired")}</p>}
      <Textarea
        aria-label={t("inputLabel")}
        placeholder={t("placeholder")}
        value={draft}
        onChange={(event) => onDraftChange(event.target.value)}
        onKeyDown={onKeyDown}
      />
      <div className="composer-toolbar">
        {model !== undefined && onModelChange ? (
          <ModelPicker
            provider={provider ?? ""}
            model={model}
            oauthProviders={oauthProviders ?? []}
            accounts={accounts ?? []}
            models={modelOptions ?? []}
            status={modelStatus ?? "ready"}
            onProviderChange={onProviderChange ?? (() => {})}
            onModelChange={onModelChange}
          />
        ) : null}
        <div className="spacer" />
        <Button type="button" variant="ghost" onClick={onSend} disabled={sendDisabled}>
          {t("send")}
        </Button>
        {busy && (
          <Button type="button" variant="ghost" onClick={onCancel}>
            {t("cancel")}
          </Button>
        )}
        {!busy && canResume && !replayRequired && (
          <Button type="button" variant="ghost" onClick={onResume}>
            {t("resume")}
          </Button>
        )}
        {replayRequired && (
          <Button type="button" variant="ghost" onClick={onReload}>
            {t("reload")}
          </Button>
        )}
      </div>
    </div>
  );
}

