import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import type { TimelineItem } from "../lib/timeline";

interface Props {
  items: TimelineItem[];
  onApprove: (requestId: string, approved: boolean, feedback: string) => void;
}

export default function Timeline({ items, onApprove }: Props) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "auto" });
  }, [items]);

  return (
    <div className="flex-1 overflow-auto px-4 py-5 lg:px-8">
      <div className="mx-auto flex max-w-3xl flex-col gap-3">
        {items.map((it) => (
          <Item key={it.id} item={it} onApprove={onApprove} />
        ))}
        <div ref={endRef} />
      </div>
    </div>
  );
}

function Agent({ id }: { id: string }) {
  return id ? (
    <span className="mr-1 text-[11px] font-medium text-accent">[{id}]</span>
  ) : null;
}

function Item({
  item,
  onApprove,
}: {
  item: TimelineItem;
  onApprove: Props["onApprove"];
}) {
  switch (item.kind) {
    case "user":
      return (
        <div className="rounded-xl border border-accent/30 bg-accent/5 px-4 py-3">
          <div className="mb-1 text-[11px] font-semibold tracking-wide text-accent uppercase">
            You
          </div>
          <div className="text-sm leading-relaxed whitespace-pre-wrap">
            {item.text}
          </div>
        </div>
      );
    case "assistant":
      return (
        <div className="rounded-xl border border-border bg-surface-1 px-4 py-3">
          <div className="mb-1 text-[11px] font-semibold tracking-wide text-muted uppercase">
            Agent
          </div>
          <Agent id={item.agentId} />
          <MarkdownText text={item.text} />
        </div>
      );
    case "thinking":
      return (
        <div className="rounded-xl border border-border/50 bg-surface-2/50 px-4 py-3 italic text-muted">
          <Agent id={item.agentId} />
          <div className="text-sm leading-relaxed whitespace-pre-wrap">
            {item.text}
          </div>
        </div>
      );
    case "tool":
      return (
        <div className="overflow-hidden rounded-xl border border-warn/40 bg-surface-1">
          <div className="flex items-center gap-2 border-b border-warn/20 bg-warn/5 px-4 py-2">
            <Agent id={item.agentId} />
            <span className="text-sm font-semibold text-warn">
              ⚡ {item.toolName}
            </span>
          </div>
          <pre className="max-h-60 overflow-auto bg-surface-0 px-4 py-3 text-xs leading-relaxed text-fg/80">
            {JSON.stringify(item.args, null, 2)}
          </pre>
          {item.result !== undefined && (
            <pre
              className={`max-h-60 overflow-auto border-t border-border px-4 py-3 text-xs leading-relaxed ${item.isError ? "text-err" : "text-fg/70"}`}
            >
              {item.result}
            </pre>
          )}
        </div>
      );
    case "approval":
      return <ApprovalCard item={item} onApprove={onApprove} />;
    case "turnEnd":
      return (
        <div className="flex items-center gap-3 px-1">
          <div className="h-px flex-1 bg-border" />
          <span
            className={`text-[11px] font-medium tracking-wide uppercase ${item.status === "completed" ? "text-ok" : "text-err"}`}
          >
            turn end · {item.status}
          </span>
          <div className="h-px flex-1 bg-border" />
        </div>
      );
    case "error":
      return (
        <div className="rounded-xl border border-err/40 bg-err/5 px-4 py-3 text-sm text-err">
          {item.text}
        </div>
      );
  }
}

function MarkdownText({ text }: { text: string }) {
  return (
    <div className="prose-webui text-sm leading-relaxed">
      <ReactMarkdown>{text}</ReactMarkdown>
    </div>
  );
}

function ApprovalCard({
  item,
  onApprove,
}: {
  item: Extract<TimelineItem, { kind: "approval" }>;
  onApprove: Props["onApprove"];
}) {
  const [feedback, setFeedback] = useState("");
  const done = !!item.resolved;
  return (
    <div className="overflow-hidden rounded-xl border border-err/40 bg-surface-1">
      <div className="border-b border-err/20 bg-err/5 px-4 py-2">
        <span className="text-[11px] font-semibold tracking-wide text-err uppercase">
          Approval Required
        </span>
      </div>
      <div className="px-4 py-3">
        <Agent id={item.agentId} />
        <span className="text-sm font-semibold text-fg">{item.toolName}</span>
        <pre className="mt-2 max-h-48 overflow-auto rounded-lg bg-surface-0 px-3 py-2 text-xs leading-relaxed text-fg/80">
          {JSON.stringify(item.args, null, 2)}
        </pre>
        {done ? (
          <div className="mt-3 text-xs text-muted">→ {item.resolved}</div>
        ) : (
          <div className="mt-3 flex items-center gap-2">
            <button
              className="rounded-lg bg-ok/90 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-ok"
              onClick={() => onApprove(item.requestId, true, feedback)}
            >
              Approve
            </button>
            <button
              className="rounded-lg border border-err/40 px-4 py-1.5 text-xs font-medium text-err transition-colors hover:bg-err/10"
              onClick={() => onApprove(item.requestId, false, feedback)}
            >
              Deny
            </button>
            <input
              className="flex-1 rounded-lg border border-border bg-surface-0 px-3 py-1.5 text-xs text-fg placeholder:text-muted focus:border-accent focus:outline-none"
              placeholder="feedback (optional)"
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
            />
          </div>
        )}
      </div>
    </div>
  );
}
