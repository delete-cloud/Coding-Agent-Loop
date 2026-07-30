import { useEffect, useRef, useState } from "react";
import type { RuntimeConfigPatch } from "../lib/api";
import type { ApprovalPolicy, ThinkingEffort } from "../lib/types";
import { PROVIDERS } from "./Header";

interface Props {
  sessionId: string;
  providerName: string | null;
  modelName: string | null;
  onUpdate: (patch: RuntimeConfigPatch) => Promise<void>;
}

type Feedback = { kind: "saved" | "error"; text: string } | null;

const inputCls =
  "w-full rounded-lg border border-border bg-surface-0 px-3 py-1.5 text-sm text-fg focus:border-accent focus:outline-none";

// Per-session runtime settings (PATCH /sessions/{id}/runtime-config). These
// are distinct from the header's new-session defaults.
export default function SettingsPanel({
  sessionId,
  providerName,
  modelName,
  onUpdate,
}: Props) {
  const [provider, setProvider] = useState(providerName ?? "");
  const [model, setModel] = useState(modelName ?? "");
  // The server does not report the session's current thinking config, so
  // these start at the schema defaults and afterwards track what the user
  // last applied (see the caption in the thinking section below).
  const [thinkingEnabled, setThinkingEnabled] = useState(true);
  const [thinkingEffort, setThinkingEffort] = useState<ThinkingEffort>("medium");
  const [feedback, setFeedback] = useState<Feedback>(null);
  const feedbackTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (feedbackTimer.current) clearTimeout(feedbackTimer.current);
    };
  }, []);

  type ApplyField = "provider" | "model" | "thinking" | "approval";
  // Per-field promise chains: rapid changes to the same control are applied
  // in order, so a stale success can never overwrite a later failure's
  // feedback (or reach the server out of order).
  const applyChains = useRef<Partial<Record<ApplyField, Promise<void>>>>({});

  const apply = (field: ApplyField, patch: RuntimeConfigPatch) => {
    const next = (applyChains.current[field] ?? Promise.resolve())
      .then(() => onUpdate(patch))
      .then(() => showFeedback({ kind: "saved", text: "Saved" }))
      .catch((e) =>
        showFeedback({
          kind: "error",
          text: `save failed: ${e instanceof Error ? e.message : String(e)}`,
        }),
      );
    applyChains.current[field] = next;
  };

  const showFeedback = (next: NonNullable<Feedback>) => {
    setFeedback(next);
    if (feedbackTimer.current) clearTimeout(feedbackTimer.current);
    feedbackTimer.current = setTimeout(() => setFeedback(null), 2500);
  };

  // The server replaces the whole thinking object (ThinkingConfigSchema fills
  // omitted keys with defaults), so always send both fields using the user's
  // last-known enabled state.
  const applyThinking = (enabled: boolean, effort: ThinkingEffort) => {
    setThinkingEnabled(enabled);
    setThinkingEffort(effort);
    apply("thinking", { thinking: { enabled, effort } });
  };

  const providerOptions = [
    ...new Set([provider, ...PROVIDERS].filter((p) => p.trim())),
  ];

  return (
    <section
      className="flex h-full min-h-0 flex-col bg-surface-1"
      aria-label={`settings for session ${sessionId}`}
    >
      <div className="flex items-center justify-between border-b border-border px-4 py-2">
        <div className="text-sm font-semibold text-fg">Session settings</div>
        {feedback && (
          <div
            className={`text-xs ${feedback.kind === "saved" ? "text-ok" : "text-err"}`}
            role="status"
          >
            {feedback.text}
          </div>
        )}
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto px-4 py-3">
        <p className="text-[11px] leading-relaxed text-muted">
          Runtime settings for the current session, applied immediately.
          Defaults for new sessions live in the header.
        </p>
        <label className="flex flex-col gap-1 text-xs text-muted">
          Provider
          <select
            className={inputCls}
            value={provider}
            title="session provider"
            onChange={(e) => {
              setProvider(e.target.value);
              if (e.target.value) apply("provider", { provider: e.target.value });
            }}
          >
            {!provider && (
              <option value="" disabled>
                select provider
              </option>
            )}
            {providerOptions.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted">
          Model
          <input
            className={`${inputCls} font-mono`}
            value={model}
            title="session model"
            placeholder="model (e.g. kimi-for-coding)"
            onChange={(e) => setModel(e.target.value)}
            onBlur={() => {
              const next = model.trim();
              if (next && next !== (modelName ?? "")) apply("model", { model: next });
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") (e.target as HTMLInputElement).blur();
            }}
          />
        </label>
        <div className="flex flex-col gap-2">
          <label className="flex items-center gap-2 text-xs text-muted">
            <input
              type="checkbox"
              checked={thinkingEnabled}
              title="thinking enabled"
              onChange={(e) => applyThinking(e.target.checked, thinkingEffort)}
            />
            Thinking
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted">
            Effort
            <select
              className={inputCls}
              value={thinkingEffort}
              title="thinking effort"
              disabled={!thinkingEnabled}
              onChange={(e) =>
                applyThinking(thinkingEnabled, e.target.value as ThinkingEffort)
              }
            >
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
            </select>
          </label>
          <p className="text-[11px] leading-relaxed text-muted">
            Initial values are defaults, not read from this session — the server
            does not report its current thinking config. Changing either control
            saves both.
          </p>
        </div>
        <label className="flex flex-col gap-1 text-xs text-muted">
          Approval policy
          <select
            className={inputCls}
            value=""
            title="session approval policy"
            onChange={(e) => apply("approval", { approval: e.target.value as ApprovalPolicy })}
          >
            <option value="" disabled>
              select policy to apply
            </option>
            <option value="auto">auto</option>
            <option value="interactive">interactive</option>
            <option value="yolo">yolo</option>
          </select>
        </label>
        <p className="text-[11px] leading-relaxed text-muted">
          The current policy is not exposed by the server. Selecting a policy
          applies it immediately and also saves it as the default for new sessions.
        </p>
      </div>
    </section>
  );
}
