"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";

import type { TimelineMessage } from "@/hooks/use-connected-chat";

/** View-level projection status; distinct from the controller's transport status. */
export type TimelineStatus = "loading" | "ready" | "error" | "reconnecting" | "replay_required";

export interface TimelineProps {
  /** View messages from timelineToMessages. When omitted (Slice 1 shell), the
   *  static placeholder copy is rendered unchanged. */
  messages?: TimelineMessage[];
  /** Present when the timeline is data-driven; drives the state affordances. */
  status?: TimelineStatus;
  /** Snapshot/load failure; rendered as an alert above stale-but-valid messages. */
  error?: unknown;
  /** Machine-readable gap reason shown with the replay-required note. */
  replayReason?: string;
}

type Translate = (key: string) => string;

function roleFor(message: TimelineMessage, t: Translate): string {
  switch (message.kind) {
    case "user":
      return t("userRole");
    case "assistant":
      return t("assistantRole");
    case "thinking":
      return t("thinkingRole");
    case "progress":
      return t("progressRole");
    case "tool":
      if (message.toolName === undefined) {
        throw new Error(`tool message ${message.id} is missing toolName`);
      }
      return message.toolError ? t("toolErrorRole") : t("toolRole");
    case "approval":
      return t("approvalRole");
    case "terminal": {
      if (message.terminalOutcome === undefined) {
        throw new Error(`terminal message ${message.id} is missing terminalOutcome`);
      }
      // Exhaustive literal-key mapping; computed keys are forbidden by 04 §4.
      switch (message.terminalOutcome) {
        case "completed":
          return t("terminalCompletedRole");
        case "failed":
          return t("terminalFailedRole");
        case "cancelled":
          return t("terminalCancelledRole");
        case "interrupted":
          return t("terminalInterruptedRole");
      }
    }
  }
}

function MessageBody({ message, t }: { message: TimelineMessage; t: Translate }) {
  switch (message.kind) {
    case "tool": {
      if (message.toolName === undefined) {
        throw new Error(`tool message ${message.id} is missing toolName`);
      }
      return (
        <>
          <p>{message.toolName}</p>
          {message.toolArguments !== undefined && (
            <p>
              <code>{message.toolArguments}</code>
            </p>
          )}
          {message.toolOutput === undefined ? (
            <p>{t("toolPending")}</p>
          ) : (
            <p>{message.toolOutput}</p>
          )}
        </>
      );
    }
    case "approval": {
      if (message.toolName === undefined) {
        throw new Error(`approval message ${message.id} is missing toolName`);
      }
      if (
        message.approvalRequestId === undefined ||
        message.effectId === undefined ||
        message.attemptId === undefined
      ) {
        throw new Error(
          `approval message ${message.id} is missing request/effect/attempt identity`,
        );
      }
      return (
        <>
          <p>{t("approvalRequired")}</p>
          <p>{message.toolName}</p>
          {message.toolArguments !== undefined && (
            <p>
              <code>{message.toolArguments}</code>
            </p>
          )}
          <p>{message.approvalRequestId}</p>
          <p>{message.effectId}</p>
          <p>{message.attemptId}</p>
          {message.approvalTargetRunId !== undefined && (
            <>
              <div className="msg-meta">{t("approvalChildTarget")}</div>
              <p>{message.approvalTargetRunId}</p>
              {message.approvalTargetParentEffectId !== undefined && (
                <p>{message.approvalTargetParentEffectId}</p>
              )}
            </>
          )}
        </>
      );
    }
    case "progress": {
      if (message.progress === undefined) {
        throw new Error(`progress message ${message.id} is missing progress`);
      }
      return (
        <>
          <p>{message.body}</p>
          <div className="msg-meta">{`${message.progress.current} / ${message.progress.total}`}</div>
        </>
      );
    }
    default:
      return <p>{message.body}</p>;
  }
}

/** RFC3339 → "HH:MM" (UTC, deterministic — matches the static shell's meta style). */
function formatTime(createdAt: string): string {
  const date = new Date(createdAt);
  if (Number.isNaN(date.getTime())) return createdAt.slice(11, 16);
  const hours = String(date.getUTCHours()).padStart(2, "0");
  const minutes = String(date.getUTCMinutes()).padStart(2, "0");
  return `${hours}:${minutes}`;
}

function MessageSection({ message, t }: { message: TimelineMessage; t: Translate }) {
  return (
    <section data-message-id={message.id}>
      <div className="role">{roleFor(message, t)}</div>
      <div className="msg">
        <MessageBody message={message} t={t} />
        <div className="msg-meta">{formatTime(message.createdAt)}</div>
      </div>
    </section>
  );
}

/**
 * Timeline — one `.timeline` container, one message list. Dynamic mode renders
 * `TimelineMessage[]` keyed by the stable source_event_id; state affordances
 * (loading / empty / error / reconnecting / replay_required) render inside the
 * same container and never introduce a new scroll surface. With no props the
 * Slice 1 placeholder messages render without invented timing or token data.
 */
export function Timeline({ messages, status, error, replayReason }: TimelineProps) {
  const t = useTranslations("timeline");

  // The disconnected shell keeps sample copy only; telemetry requires real events.
  if (messages === undefined && status === undefined) {
    const placeholder = [
      { role: t("userRole"), body: t("m1.body") },
      { role: t("assistantRole"), body: t("m2.body") },
      { role: t("userRole"), body: t("m3.body") },
      { role: t("assistantRole"), body: t("m4.body") },
    ];
    return (
      <div className="timeline">
        {placeholder.map((message, index) => (
          <section key={index}>
            <div className="role">{message.role}</div>
            <div className="msg">
              <p>{message.body}</p>
            </div>
          </section>
        ))}
      </div>
    );
  }

  const list = messages ?? [];

  if (status === "loading") {
    return (
      <div className="timeline">
        <div className="msg">
          <p>{t("loading")}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="timeline">
      {status === "error" && (
        <div className="msg" role="alert">
          <p>{t("error")}</p>
          {error != null && (
            <p>{error instanceof Error ? error.message : String(error)}</p>
          )}
        </div>
      )}
      {status === "reconnecting" && (
        <div className="msg">
          <p>{t("reconnecting")}</p>
        </div>
      )}
      {status === "replay_required" && (
        <div className="msg">
          <p>{t("replayRequired")}</p>
          {replayReason !== undefined && <div className="msg-meta">{replayReason}</div>}
        </div>
      )}
      {status === "ready" && list.length === 0 && (
        <div className="msg">
          <p>{t("empty")}</p>
        </div>
      )}
      {list.map((message) => (
        <MessageSection key={message.id} message={message} t={t} />
      ))}
    </div>
  );
}

function trajectoryError(message: TimelineMessage): string {
  if (message.kind === "terminal" && message.terminalOutcome === "failed") return message.body;
  if (message.toolError) return message.toolOutput ?? message.body;
  return "";
}

/** Event ledger / trace view of the same timeline messages. */
export function TrajectoryLedger({ messages, status, error, replayReason }: TimelineProps) {
  const t = useTranslations("timeline");
  const tConv = useTranslations("conversation");
  const [openId, setOpenId] = useState<string | null>(null);
  const list = messages ?? [];

  if (status === "loading") {
    return (
      <div className="trajectory">
        <div className="msg">
          <p>{t("loading")}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="trajectory">
      {status === "error" && (
        <div className="msg" role="alert">
          <p>{t("error")}</p>
          {error != null && <p>{error instanceof Error ? error.message : String(error)}</p>}
        </div>
      )}
      {status === "reconnecting" && (
        <div className="msg">
          <p>{t("reconnecting")}</p>
        </div>
      )}
      {status === "replay_required" && (
        <div className="msg">
          <p>{t("replayRequired")}</p>
          {replayReason !== undefined && <div className="msg-meta">{replayReason}</div>}
        </div>
      )}
      {status === "ready" && list.length === 0 && (
        <div className="msg">
          <p>{t("empty")}</p>
        </div>
      )}
      {list.length > 0 && (
        <table className="trajectory-table">
          <thead>
            <tr>
              <th>{tConv("seq")}</th>
              <th>{tConv("kind")}</th>
              <th>{tConv("summary")}</th>
              <th>{tConv("error")}</th>
            </tr>
          </thead>
          <tbody>
            {list.map((message, index) => {
              const err = trajectoryError(message);
              return (
                <tr
                  key={message.id}
                  className="trajectory-row"
                  onClick={() => setOpenId(openId === message.id ? null : message.id)}
                >
                  <td>{index + 1}</td>
                  <td>{message.kind}</td>
                  <td>{message.body}</td>
                  <td>{err}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {openId !== null &&
        list
          .filter((message) => message.id === openId)
          .map((message) => (
            <pre key={message.id} className="trajectory-payload">
              {JSON.stringify(message, null, 2)}
            </pre>
          ))}
    </div>
  );
}
