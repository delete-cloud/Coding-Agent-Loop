"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { isNotFoundError } from "@/lib/connected-chat/client";
import type { CodexFlow, OAuthAccount } from "@/lib/connected-chat/wire";

export interface CodexClient {
  listOAuthAccounts(): Promise<OAuthAccount[]>;
  listCodexFlows(): Promise<CodexFlow[]>;
  startCodexOAuth(label?: string): Promise<{
    flow_id: string;
    verification_url: string;
    user_code: string;
    expires_in: number;
  }>;
  getCodexFlow(flowId: string): Promise<CodexFlow>;
  cancelCodexFlow(flowId: string): Promise<void>;
  deleteOAuthAccount(providerKey: string): Promise<void>;
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function CodexAccountsCard({
  client,
  pollMs = 3000,
}: {
  client: CodexClient;
  pollMs?: number;
}) {
  const t = useTranslations("codex");
  const [accounts, setAccounts] = useState<OAuthAccount[] | null>(null);
  const [flows, setFlows] = useState<CodexFlow[]>([]);
  const [unsupported, setUnsupported] = useState(false);
  const [label, setLabel] = useState("");
  const [starting, setStarting] = useState(false);
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [copiedFlow, setCopiedFlow] = useState<string | null>(null);
  const handledAuthorized = useRef(new Set<string>());
  const pollFailures = useRef(new Map<string, number>());

  const refreshAccounts = useCallback(async () => {
    try {
      setAccounts(await client.listOAuthAccounts());
    } catch (error) {
      if (isNotFoundError(error)) setUnsupported(true);
      else setNotice({ kind: "error", text: errorText(error) });
    }
  }, [client]);

  useEffect(() => {
    let alive = true;
    void refreshAccounts();
    client
      .listCodexFlows()
      .then((list) => {
        if (alive) setFlows(list);
      })
      .catch((error) => {
        if (isNotFoundError(error)) setUnsupported(true);
      });
    return () => {
      alive = false;
    };
  }, [client, refreshAccounts]);

  useEffect(() => {
    if (unsupported) return;
    const pending = flows.filter((flow) => flow.state === "pending");
    if (pending.length === 0) return;
    const timer = setInterval(() => {
      for (const flow of pending) {
        client
          .getCodexFlow(flow.flow_id)
          .then((next) => {
            pollFailures.current.delete(next.flow_id);
            setFlows((prev) => prev.map((item) => (item.flow_id === next.flow_id ? next : item)));
            if (next.state === "authorized" && !handledAuthorized.current.has(next.flow_id)) {
              handledAuthorized.current.add(next.flow_id);
              setNotice({
                kind: "ok",
                text: next.account_label ?? next.flow_id,
              });
              void refreshAccounts();
            }
          })
          .catch((error) => {
            if (isNotFoundError(error)) {
              setFlows((prev) =>
                prev.map((item) =>
                  item.flow_id === flow.flow_id
                    ? { ...item, state: "expired", error: t("expired") }
                    : item,
                ),
              );
              return;
            }
            const fails = (pollFailures.current.get(flow.flow_id) ?? 0) + 1;
            pollFailures.current.set(flow.flow_id, fails);
            if (fails >= 5) {
              setFlows((prev) =>
                prev.map((item) =>
                  item.flow_id === flow.flow_id
                    ? { ...item, state: "error", error: errorText(error) }
                    : item,
                ),
              );
            }
          });
      }
    }, pollMs);
    return () => clearInterval(timer);
  }, [client, flows, pollMs, unsupported, refreshAccounts, t]);

  const startFlow = async () => {
    setStarting(true);
    setNotice(null);
    try {
      const started = await client.startCodexOAuth(label.trim() || undefined);
      setFlows((prev) => [
        {
          flow_id: started.flow_id,
          state: "pending",
          verification_url: started.verification_url,
          user_code: started.user_code,
        },
        ...prev.filter((item) => item.flow_id !== started.flow_id),
      ]);
    } catch (error) {
      if (isNotFoundError(error)) setUnsupported(true);
      else setNotice({ kind: "error", text: errorText(error) });
    } finally {
      setStarting(false);
    }
  };

  const cancelFlow = async (flowId: string) => {
    try {
      await client.cancelCodexFlow(flowId);
      setFlows((prev) =>
        prev.map((item) => (item.flow_id === flowId ? { ...item, state: "cancelled" } : item)),
      );
    } catch (error) {
      setNotice({ kind: "error", text: errorText(error) });
    }
  };

  const disconnect = async (providerKey: string) => {
    if (!window.confirm(providerKey)) return;
    try {
      await client.deleteOAuthAccount(providerKey);
      await refreshAccounts();
    } catch (error) {
      setNotice({ kind: "error", text: errorText(error) });
    }
  };

  const copyCode = (flowId: string, code: string) => {
    void navigator.clipboard?.writeText(code).then(() => {
      setCopiedFlow(flowId);
      setTimeout(() => setCopiedFlow((current) => (current === flowId ? null : current)), 1500);
    });
  };

  const visibleFlows = flows.filter(
    (flow) => flow.state !== "authorized" && flow.state !== "cancelled",
  );

  if (unsupported) {
    return (
      <section aria-label={t("title")} className="settings-section">
        <h4 className="details-h4">{t("title")}</h4>
        <p className="panel-note">{t("unsupported")}</p>
      </section>
    );
  }

  return (
    <section aria-label={t("title")} className="settings-section">
      <h4 className="details-h4">{t("title")}</h4>
      {notice ? (
        <p className={notice.kind === "ok" ? "settings-ok" : "settings-err"} role="status">
          {notice.text}
        </p>
      ) : null}
      {accounts === null ? (
        <p className="panel-note">{t("loading")}</p>
      ) : accounts.length === 0 ? (
        <p className="panel-note">{t("empty")}</p>
      ) : (
        <ul className="settings-list">
          {accounts.map((account) => (
            <li key={account.provider} className="settings-account">
              <div className="settings-account-meta">
                <span>{account.label}</span>
                <span className="set-row-k">
                  {account.email ? `${account.email} · ` : ""}
                  {account.provider}
                </span>
              </div>
              <Button
                variant="ghost"
                className="settings-inline-btn"
                title={`${t("disconnect")} ${account.provider}`}
                onClick={() => void disconnect(account.provider)}
              >
                {t("disconnect")}
              </Button>
            </li>
          ))}
        </ul>
      )}

      {visibleFlows.length > 0 ? (
        <ul className="settings-list" aria-label={t("flowsLabel")}>
          {visibleFlows.map((flow) => (
            <li key={flow.flow_id} className="settings-flow">
              {flow.state === "pending" ? (
                <>
                  <p className="panel-note">
                    {t("openPrefix")}{" "}
                    {flow.verification_url ? (
                      <a href={flow.verification_url} target="_blank" rel="noreferrer">
                        {flow.verification_url}
                      </a>
                    ) : (
                      t("verificationPage")
                    )}{" "}
                    {t("openSuffix")}
                  </p>
                  {flow.user_code ? (
                    <Button
                      variant="ghost"
                      className="settings-code"
                      title={t("copyCode")}
                      onClick={() => copyCode(flow.flow_id, flow.user_code ?? "")}
                    >
                      {copiedFlow === flow.flow_id ? t("copied") : flow.user_code}
                    </Button>
                  ) : null}
                  <div className="settings-flow-actions">
                    <span className="panel-note">{t("waiting")}</span>
                    <Button
                      variant="ghost"
                      className="settings-inline-btn"
                      onClick={() => void cancelFlow(flow.flow_id)}
                    >
                      {t("cancel")}
                    </Button>
                  </div>
                </>
              ) : (
                <>
                  <p className="settings-err">
                    {flow.state === "expired" ? (flow.error ?? t("expired")) : `${t("failed")}: ${flow.error ?? ""}`}
                  </p>
                  <div className="settings-flow-actions">
                    <Button
                      variant="ghost"
                      className="settings-inline-btn"
                      onClick={() => {
                        setFlows((prev) => prev.filter((item) => item.flow_id !== flow.flow_id));
                        void startFlow();
                      }}
                    >
                      {t("retry")}
                    </Button>
                    <Button
                      variant="ghost"
                      className="settings-inline-btn"
                      onClick={() =>
                        setFlows((prev) => prev.filter((item) => item.flow_id !== flow.flow_id))
                      }
                    >
                      {t("dismiss")}
                    </Button>
                  </div>
                </>
              )}
            </li>
          ))}
        </ul>
      ) : null}

      <div className="settings-add">
        <Input
          value={label}
          onChange={(event) => setLabel(event.target.value)}
          placeholder={t("labelPlaceholder")}
          title={t("labelTitle")}
        />
        <Button disabled={starting} onClick={() => void startFlow()}>
          {starting ? t("starting") : t("add")}
        </Button>
      </div>
    </section>
  );
}
