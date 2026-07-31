import { useCallback, useEffect, useRef, useState } from "react";
import type { AgentClient } from "../lib/api";
import type { CodexFlow, OAuthAccount } from "../lib/types";

const inputCls =
  "w-full rounded-lg border border-border bg-surface-0 px-3 py-1.5 text-sm text-fg focus:border-accent focus:outline-none";
const btnCls =
  "rounded-lg border border-border px-2.5 py-1 text-xs text-fg transition-colors hover:border-border-active disabled:opacity-40";

// Older servers predate the /oauth/* endpoints; detect their 404s once and
// degrade the whole card to a note instead of per-action errors.
const isNotFound = (e: unknown) => e instanceof Error && /^404\b/.test(e.message);
const msg = (e: unknown) => (e instanceof Error ? e.message : String(e));

interface Props {
  client: AgentClient;
  // Poll interval for pending flows; overridable for tests.
  pollMs?: number;
}

// "Codex accounts" settings card: lists connected ChatGPT OAuth accounts and
// manages device-code login flows (start / poll / cancel / recover on reload).
export default function CodexAccountsCard({ client, pollMs = 3000 }: Props) {
  const [accounts, setAccounts] = useState<OAuthAccount[] | null>(null);
  const [flows, setFlows] = useState<CodexFlow[]>([]);
  const [unsupported, setUnsupported] = useState(false);
  const [label, setLabel] = useState("");
  const [starting, setStarting] = useState(false);
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [copiedFlow, setCopiedFlow] = useState<string | null>(null);
  // Flows whose "authorized" transition was already handled, so a re-render
  // or an extra poll tick cannot refresh the accounts list twice.
  const handledAuthorized = useRef(new Set<string>());
  // Consecutive poll-failure count per flow; gives up after 5 (see poll effect).
  const pollFailures = useRef(new Map<string, number>());

  const refreshAccounts = useCallback(async () => {
    try {
      setAccounts(await client.listOAuthAccounts());
    } catch (e) {
      if (isNotFound(e)) setUnsupported(true);
      else setNotice({ kind: "error", text: `failed to load accounts: ${msg(e)}` });
    }
  }, [client]);

  // Initial load: accounts plus any in-flight flows (page-reload recovery).
  useEffect(() => {
    let alive = true;
    void refreshAccounts();
    client
      .listCodexFlows()
      .then((list) => {
        if (alive) setFlows(list);
      })
      .catch((e) => {
        if (isNotFound(e)) setUnsupported(true);
      });
    return () => {
      alive = false;
    };
  }, [client, refreshAccounts]);

  // Poll every pending flow until it leaves "pending".
  useEffect(() => {
    if (unsupported) return;
    const pending = flows.filter((f) => f.state === "pending");
    if (pending.length === 0) return;
    const timer = setInterval(() => {
      for (const flow of pending) {
        client
          .getCodexFlow(flow.flow_id)
          .then((next) => {
            pollFailures.current.delete(next.flow_id);
            setFlows((prev) => prev.map((f) => (f.flow_id === next.flow_id ? next : f)));
            if (next.state === "authorized" && !handledAuthorized.current.has(next.flow_id)) {
              handledAuthorized.current.add(next.flow_id);
              setNotice({
                kind: "ok",
                text: `Connected ${next.account_label ?? "codex account"}`,
              });
              void refreshAccounts();
            }
          })
          .catch((e) => {
            // Server lost the flow (restart cleared the in-memory registry or
            // TTL pruned it): stop polling it instead of retrying forever.
            if (isNotFound(e)) {
              setFlows((prev) =>
                prev.map((f) =>
                  f.flow_id === flow.flow_id
                    ? { ...f, state: "expired" as const, error: "server lost this login flow; start a new one" }
                    : f,
                ),
              );
              return;
            }
            // Other errors (auth failure, network): tolerate a few transient
            // failures, then give up instead of polling until page close.
            const fails = (pollFailures.current.get(flow.flow_id) ?? 0) + 1;
            pollFailures.current.set(flow.flow_id, fails);
            if (fails >= 5) {
              setFlows((prev) =>
                prev.map((f) =>
                  f.flow_id === flow.flow_id
                    ? { ...f, state: "error" as const, error: `polling failed: ${msg(e)}` }
                    : f,
                ),
              );
            }
          });
      }
    }, pollMs);
    return () => clearInterval(timer);
  }, [client, flows, pollMs, unsupported, refreshAccounts]);

  const startFlow = async () => {
    setStarting(true);
    setNotice(null);
    try {
      const res = await client.startCodexOAuth(label.trim() || undefined);
      setFlows((prev) => [
        {
          flow_id: res.flow_id,
          state: "pending",
          verification_url: res.verification_url,
          user_code: res.user_code,
        },
        ...prev.filter((f) => f.flow_id !== res.flow_id),
      ]);
    } catch (e) {
      if (isNotFound(e)) setUnsupported(true);
      else setNotice({ kind: "error", text: `start failed: ${msg(e)}` });
    } finally {
      setStarting(false);
    }
  };

  const cancelFlow = async (flowId: string) => {
    try {
      await client.cancelCodexFlow(flowId);
      setFlows((prev) =>
        prev.map((f) => (f.flow_id === flowId ? { ...f, state: "cancelled" } : f)),
      );
    } catch (e) {
      setNotice({ kind: "error", text: `cancel failed: ${msg(e)}` });
    }
  };

  const disconnect = async (providerKey: string) => {
    if (
      !window.confirm(
        `Disconnect ${providerKey}? This removes the local OAuth record (no remote revoke).`,
      )
    ) {
      return;
    }
    try {
      await client.deleteOAuthAccount(providerKey);
      await refreshAccounts();
    } catch (e) {
      setNotice({ kind: "error", text: `disconnect failed: ${msg(e)}` });
    }
  };

  const copyCode = (flowId: string, code: string) => {
    void navigator.clipboard?.writeText(code).then(() => {
      setCopiedFlow(flowId);
      setTimeout(() => setCopiedFlow((cur) => (cur === flowId ? null : cur)), 1500);
    });
  };

  // Retry after error/expired: drop the dead flow locally and start fresh.
  const retryFlow = (flowId: string) => {
    setFlows((prev) => prev.filter((f) => f.flow_id !== flowId));
    void startFlow();
  };

  const visibleFlows = flows.filter((f) => f.state !== "authorized" && f.state !== "cancelled");

  if (unsupported) {
    return (
      <section aria-label="codex accounts" className="flex flex-col gap-2">
        <div className="text-xs font-semibold text-fg">Codex accounts</div>
        <p className="text-[11px] leading-relaxed text-muted">
          Codex OAuth is not supported by this server.
        </p>
      </section>
    );
  }

  return (
    <section aria-label="codex accounts" className="flex flex-col gap-2">
      <div className="text-xs font-semibold text-fg">Codex accounts</div>
      {notice && (
        <div
          className={`text-[11px] ${notice.kind === "ok" ? "text-ok" : "text-err"}`}
          role="status"
        >
          {notice.text}
        </div>
      )}
      {accounts === null ? (
        <p className="text-[11px] text-muted">Loading accounts…</p>
      ) : accounts.length === 0 ? (
        <p className="text-[11px] text-muted">No codex accounts connected.</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {accounts.map((a) => (
            <li
              key={a.provider}
              className="flex items-center gap-2 rounded-lg border border-border bg-surface-0 px-2.5 py-1.5"
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <span className="truncate text-xs font-medium text-fg">{a.label}</span>
                  {a.plan && (
                    <span className="rounded-full border border-border px-1.5 py-px text-[10px] text-muted">
                      {a.plan}
                    </span>
                  )}
                </div>
                <div className="truncate text-[10px] text-muted">
                  {a.email ? `${a.email} · ` : ""}
                  {a.provider}
                </div>
              </div>
              <button
                type="button"
                className={btnCls}
                title={`disconnect ${a.provider}`}
                onClick={() => void disconnect(a.provider)}
              >
                Disconnect
              </button>
            </li>
          ))}
        </ul>
      )}

      {visibleFlows.length > 0 && (
        <ul className="flex flex-col gap-1.5" aria-label="in-flight login flows">
          {visibleFlows.map((f) => (
            <li
              key={f.flow_id}
              className="flex flex-col gap-1.5 rounded-lg border border-border bg-surface-0 px-2.5 py-2"
            >
              {f.state === "pending" ? (
                <>
                  <div className="text-[11px] text-muted">
                    Open{" "}
                    {f.verification_url ? (
                      <a
                        className="text-accent underline"
                        href={f.verification_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        {f.verification_url}
                      </a>
                    ) : (
                      "the verification page"
                    )}{" "}
                    and enter this code:
                  </div>
                  {f.user_code && (
                    <button
                      type="button"
                      className="self-start rounded border border-border bg-surface-1 px-2 py-1 font-mono text-base tracking-widest text-fg"
                      title="click to copy code"
                      onClick={() => copyCode(f.flow_id, f.user_code ?? "")}
                    >
                      {copiedFlow === f.flow_id ? "copied!" : f.user_code}
                    </button>
                  )}
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] text-muted">waiting for authorization…</span>
                    <button
                      type="button"
                      className={btnCls}
                      title={`cancel flow ${f.flow_id}`}
                      onClick={() => void cancelFlow(f.flow_id)}
                    >
                      Cancel
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <div className="text-[11px] text-err">
                    {f.state === "expired"
                      ? (f.error ?? "Login flow expired before authorization.")
                      : `Login failed: ${f.error ?? "unknown error"}`}
                  </div>
                  <div className="flex gap-1.5">
                    <button
                      type="button"
                      className={btnCls}
                      title={`retry flow ${f.flow_id}`}
                      onClick={() => retryFlow(f.flow_id)}
                    >
                      Retry
                    </button>
                    <button
                      type="button"
                      className={btnCls}
                      title={`dismiss flow ${f.flow_id}`}
                      onClick={() =>
                        setFlows((prev) => prev.filter((x) => x.flow_id !== f.flow_id))
                      }
                    >
                      Dismiss
                    </button>
                  </div>
                </>
              )}
            </li>
          ))}
        </ul>
      )}

      <div className="flex items-center gap-1.5">
        <input
          className={inputCls}
          placeholder="account label (optional)"
          title="new account label"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
        />
        <button
          type="button"
          className={btnCls}
          disabled={starting}
          onClick={() => void startFlow()}
        >
          {starting ? "Starting…" : "Add account"}
        </button>
      </div>
    </section>
  );
}
