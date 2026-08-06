import { useState } from "react";
import ReactMarkdown from "react-markdown";
import type { SessionResult } from "../lib/types";

interface Props {
  result: SessionResult;
}

// True when GET /sessions/{id}/result has anything worth showing.
export function hasResultContent(result: SessionResult): boolean {
  return Boolean(
    result.final_answer?.trim() ||
      result.verification_summary?.trim() ||
      result.failure_details?.trim(),
  );
}

// Collapsible session result block pinned above the timeline.
export default function ResultPanel({ result }: Props) {
  const [open, setOpen] = useState(true);
  const failed = Boolean(result.failure_details?.trim());
  const meta = [result.provider_name, result.model_name].filter(Boolean).join(" / ");

  return (
    <div className="shrink-0 border-b border-border bg-surface-1 px-4 py-2 lg:px-8">
      <div className="mx-auto max-w-3xl">
        <button
          type="button"
          className="flex w-full items-center gap-2 text-left"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          <span
            className={`h-2 w-2 shrink-0 rounded-full ${failed ? "bg-err" : result.status === "completed" ? "bg-ok" : "bg-muted"}`}
            title={`status: ${result.status}`}
          />
          <span className="text-xs font-semibold tracking-wide text-fg uppercase">
            Session result
          </span>
          <span className="text-[11px] text-muted">{result.status}</span>
          {meta && <span className="truncate font-mono text-[11px] text-muted">{meta}</span>}
          <span className="ml-auto shrink-0 text-[10px] text-muted" aria-hidden>
            {open ? "▾" : "▸"}
          </span>
        </button>
        {open && (
          <div
            className="mt-2 flex max-h-[40vh] flex-col gap-2 overflow-y-auto pb-1"
            role="region"
            aria-label="Session result details"
            tabIndex={0}
          >
            {result.final_answer?.trim() && (
              <div className="prose-webui text-sm leading-relaxed">
                <ReactMarkdown>{result.final_answer}</ReactMarkdown>
              </div>
            )}
            {result.verification_summary?.trim() && (
              <div className="rounded-lg border border-border bg-surface-0 px-3 py-2 text-xs text-fg/80">
                <span className="mr-1.5 font-semibold text-muted uppercase">verification</span>
                {result.verification_summary}
              </div>
            )}
            {result.failure_details?.trim() && (
              <div className="rounded-lg border border-err/40 bg-err/5 px-3 py-2 text-xs whitespace-pre-wrap text-err">
                {result.failure_details}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
