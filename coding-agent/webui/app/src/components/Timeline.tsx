import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { toolArgSummary, toolDuration, type TimelineItem } from "../lib/timeline";
import type { ApprovalScope } from "../lib/types";

interface Props {
  items: TimelineItem[];
  onApprove: (
    requestId: string,
    approved: boolean,
    feedback: string,
    scope: ApprovalScope,
  ) => void;
  showThinking: boolean;
}

type ToolItem = Extract<TimelineItem, { kind: "tool" }>;
type TimelineNode =
  | TimelineItem
  | { id: string; kind: "toolGroup"; tools: ToolItem[] };

// Fold runs of consecutive tool items (>= 2) into a single group node; lone
// tool calls stay standalone.
function groupToolItems(items: TimelineItem[]): TimelineNode[] {
  const nodes: TimelineNode[] = [];
  let run: ToolItem[] = [];
  const flush = () => {
    if (run.length >= 2) {
      nodes.push({ id: `g-${run[0].id}`, kind: "toolGroup", tools: run });
    } else {
      nodes.push(...run);
    }
    run = [];
  };
  for (const it of items) {
    if (it.kind === "tool") {
      run.push(it);
    } else {
      flush();
      nodes.push(it);
    }
  }
  flush();
  return nodes;
}

export default function Timeline({ items, onApprove, showThinking }: Props) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "auto" });
  }, [items]);

  const visible = showThinking
    ? items
    : items.filter((it) => it.kind !== "thinking");
  const nodes = groupToolItems(visible);

  return (
    <div className="flex-1 overflow-auto px-4 py-5 lg:px-8">
      <div className="mx-auto flex max-w-3xl flex-col gap-3">
        {nodes.map((node) =>
          node.kind === "toolGroup" ? (
            <ToolGroup key={node.id} tools={node.tools} />
          ) : (
            <Item key={node.id} item={node} onApprove={onApprove} />
          ),
        )}
        <div ref={endRef} />
      </div>
    </div>
  );
}

function Agent({ id }: { id: string }) {
  if (!id) return null;
  return (
    <>
      <span className="mr-1 text-[11px] font-medium text-accent">[{id}]</span>
      <span className="mr-1 rounded-full border border-accent/40 px-1.5 py-px text-[10px] font-medium text-accent">
        subagent
      </span>
    </>
  );
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
      return <ToolCard item={item} />;
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

function ToolGroup({ tools }: { tools: ToolItem[] }) {
  const [open, setOpen] = useState(true);
  const running = tools.filter((t) => t.result === undefined).length;
  return (
    <div className="overflow-hidden rounded-xl border border-border/60 bg-surface-2/40">
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[11px] font-medium tracking-wide text-muted uppercase transition-colors hover:text-fg"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span aria-hidden>{open ? "▾" : "▸"}</span>
        <span>
          {tools.length} tool call{tools.length === 1 ? "" : "s"}
        </span>
        {running > 0 && (
          <span className="text-accent normal-case">· {running} running</span>
        )}
      </button>
      {open && (
        <div className="flex flex-col gap-1.5 px-2 pb-2">
          {tools.map((t) => (
            <ToolCard key={t.id} item={t} />
          ))}
        </div>
      )}
    </div>
  );
}

function ToolCard({ item }: { item: ToolItem }) {
  const [expanded, setExpanded] = useState(false);
  const running = item.result === undefined;
  const summary = toolArgSummary(item.toolName, item.args);
  const duration = toolDuration(item);
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface-1">
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left transition-colors hover:bg-surface-2/60"
        aria-expanded={expanded}
        onClick={() => setExpanded((v) => !v)}
      >
        {running ? (
          <span
            className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-accent"
            title="running"
          />
        ) : item.isError ? (
          <span className="shrink-0 text-xs text-err" title="failed">
            ✗
          </span>
        ) : (
          <span className="shrink-0 text-xs text-ok" title="done">
            ✓
          </span>
        )}
        <Agent id={item.agentId} />
        <span className="shrink-0 text-xs font-semibold text-fg">
          {item.toolName}
        </span>
        {summary && (
          <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted">
            {summary}
          </span>
        )}
        {duration && (
          <span className="shrink-0 text-[11px] text-muted">{duration}</span>
        )}
        <span className="shrink-0 text-[10px] text-muted" aria-hidden>
          {expanded ? "▾" : "▸"}
        </span>
      </button>
      {expanded && (
        <div className="border-t border-border/60">
          <pre className="max-h-60 overflow-auto bg-surface-0 px-3 py-2 text-xs leading-relaxed text-fg/80">
            {JSON.stringify(item.args, null, 2)}
          </pre>
          {item.result !== undefined && (
            <pre
              className={`max-h-60 overflow-auto border-t border-border px-3 py-2 text-xs leading-relaxed ${item.isError ? "text-err" : "text-fg/70"}`}
            >
              {item.result}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

function MarkdownText({ text }: { text: string }) {
  return (
    <div className="prose-webui text-sm leading-relaxed">
      <ReactMarkdown>{text}</ReactMarkdown>
    </div>
  );
}

function formatClock(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function ApprovalCard({
  item,
  onApprove,
}: {
  item: Extract<TimelineItem, { kind: "approval" }>;
  onApprove: Props["onApprove"];
}) {
  const [feedback, setFeedback] = useState("");
  // Deadline (ms epoch) mirroring the server-side auto-deny: the server counts
  // from the prompt's created_at, not from when this card mounts, so a
  // replayed or reopened prompt does not restart the countdown. Items without
  // a promptedAt (defensive fallback) count from mount.
  const [deadline] = useState<number | null>(() => {
    if (item.timeoutSeconds === undefined) return null;
    const start = item.promptedAt ? new Date(item.promptedAt).getTime() : NaN;
    const base = Number.isFinite(start) ? start : Date.now();
    return base + item.timeoutSeconds * 1000;
  });
  const computeRemaining = () =>
    deadline === null
      ? null
      : Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
  const [remaining, setRemaining] = useState<number | null>(computeRemaining);
  const timedOut = remaining !== null && remaining <= 0;
  const done = !!item.resolved || timedOut;

  // Recompute from the wall clock each tick instead of decrementing, so
  // background-tab timer throttling cannot lag the countdown behind the
  // server deadline.
  useEffect(() => {
    if (done || deadline === null) return;
    const tick = () => setRemaining(computeRemaining());
    const timer = setInterval(tick, 1000);
    document.addEventListener("visibilitychange", tick);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", tick);
    };
  }, [done, deadline]);

  return (
    <div className="overflow-hidden rounded-xl border border-err/40 bg-surface-1">
      <div className="flex items-center justify-between border-b border-err/20 bg-err/5 px-4 py-2">
        <span className="text-[11px] font-semibold tracking-wide text-err uppercase">
          Approval Required
        </span>
        {!done && remaining !== null && (
          <span className="font-mono text-[11px] text-err/80">
            {formatClock(remaining)}
          </span>
        )}
      </div>
      <div className="px-4 py-3">
        <Agent id={item.agentId} />
        <span className="text-sm font-semibold text-fg">{item.toolName}</span>
        <pre className="mt-2 max-h-48 overflow-auto rounded-lg bg-surface-0 px-3 py-2 text-xs leading-relaxed text-fg/80">
          {JSON.stringify(item.args, null, 2)}
        </pre>
        {done ? (
          <div className="mt-3 text-xs text-muted">
            → {timedOut && !item.resolved ? "timed out (auto-denied)" : item.resolved}
          </div>
        ) : (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              className="rounded-lg bg-ok/90 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-ok"
              onClick={() => onApprove(item.requestId, true, feedback, "once")}
            >
              Approve
            </button>
            <button
              className="rounded-lg border border-ok/40 px-4 py-1.5 text-xs font-medium text-ok transition-colors hover:bg-ok/10"
              title="Approve and auto-approve this tool for the rest of the session"
              onClick={() => onApprove(item.requestId, true, feedback, "session")}
            >
              Always allow (this session)
            </button>
            <button
              className="rounded-lg border border-err/40 px-4 py-1.5 text-xs font-medium text-err transition-colors hover:bg-err/10"
              onClick={() => onApprove(item.requestId, false, feedback, "once")}
            >
              Deny
            </button>
            <input
              className="min-w-32 flex-1 rounded-lg border border-border bg-surface-0 px-3 py-1.5 text-xs text-fg placeholder:text-muted focus:border-accent focus:outline-none"
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
