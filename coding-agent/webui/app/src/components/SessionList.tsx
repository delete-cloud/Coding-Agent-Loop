import type { SessionSummary } from "../lib/types";

interface Props {
  sessions: SessionSummary[];
  activeSessionId: string | null;
  loading: boolean;
  error: string | null;
  open: boolean;
  onRefresh: () => void;
  onSelect: (sessionId: string) => void;
  onDelete: (sessionId: string) => void;
  onNewSession: () => void;
}

export default function SessionList({
  sessions,
  activeSessionId,
  loading,
  error,
  open,
  onRefresh,
  onSelect,
  onDelete,
  onNewSession,
}: Props) {
  return (
    <aside
      className={`fixed inset-y-0 left-0 z-40 w-[260px] shrink-0 flex-col border-r border-border bg-surface-1 md:static md:z-auto ${
        open ? "flex" : "hidden"
      }`}
      aria-label="Sessions"
    >
      <div className="flex items-center justify-between border-b border-border px-3 py-2.5">
        <div className="text-xs font-semibold tracking-wide text-muted uppercase">
          Sessions
        </div>
        <div className="flex items-center gap-1.5">
          <button
            className="rounded-md border border-border px-2 py-1 text-xs text-fg transition-colors hover:border-border-active"
            onClick={onNewSession}
          >
            + New
          </button>
          <button
            className="rounded-md border border-border px-2 py-1 text-xs text-fg transition-colors hover:border-border-active disabled:opacity-40"
            disabled={loading}
            onClick={onRefresh}
          >
            Refresh
          </button>
        </div>
      </div>
      {error && (
        <div className="border-b border-err/30 bg-err/5 px-3 py-2 text-xs text-err">
          {error}
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-auto">
        {sessions.length === 0 && !loading ? (
          <div className="px-3 py-4 text-sm text-muted">No sessions</div>
        ) : (
          sessions.map((session) => (
            <SessionRow
              key={session.session_id}
              session={session}
              active={session.session_id === activeSessionId}
              onSelect={onSelect}
              onDelete={onDelete}
            />
          ))
        )}
      </div>
    </aside>
  );
}

function SessionRow({
  session,
  active,
  onSelect,
  onDelete,
}: {
  session: SessionSummary;
  active: boolean;
  onSelect: (sessionId: string) => void;
  onDelete: (sessionId: string) => void;
}) {
  return (
    <div
      className={`flex items-stretch border-b border-border transition-colors hover:bg-surface-2 ${
        active ? "bg-accent/10" : ""
      }`}
    >
      <button
        className="block min-w-0 flex-1 px-3 py-2.5 text-left"
        onClick={() => onSelect(session.session_id)}
      >
        <div className="flex items-center gap-2">
          <StatusDot status={session.status} pending={session.pending_approval} />
          <span className="truncate font-mono text-sm font-medium text-fg">
            {shortId(session.session_id)}
          </span>
          {session.checkpoint_count > 0 && (
            <span
              className="shrink-0 rounded border border-border px-1 py-0.5 text-[10px] leading-none text-muted"
              title={`${session.checkpoint_count} checkpoint${session.checkpoint_count === 1 ? "" : "s"}`}
            >
              ⎘ {session.checkpoint_count}
            </span>
          )}
          {session.turn_in_progress && (
            <span className="shrink-0 rounded bg-accent/10 px-1.5 py-0.5 text-[10px] font-medium text-accent">
              running
            </span>
          )}
        </div>
        <div className="mt-1 truncate font-mono text-xs text-muted">
          {formatModel(session)}
        </div>
        <div className="mt-1 text-[11px] text-muted">
          {formatTime(session.last_activity)}
        </div>
      </button>
      <button
        className="shrink-0 px-2 text-muted transition-colors hover:text-err"
        aria-label={`Close session ${shortId(session.session_id)}`}
        title="Close session"
        onClick={() => onDelete(session.session_id)}
      >
        ×
      </button>
    </div>
  );
}

function formatModel(session: SessionSummary) {
  const parts = [session.provider_name, session.model_name].filter(Boolean);
  return parts.length ? parts.join(" / ") : "model ?";
}

type StatusSemantic = "running" | "pending" | "failed" | "idle";

function statusSemantic(status: SessionSummary["status"], pending: boolean): StatusSemantic {
  if (pending) return "pending";
  if (status === "failed") return "failed";
  if (status === "running" || status === "waiting_approval") return "running";
  return "idle";
}

function StatusDot({
  status,
  pending,
}: {
  status: SessionSummary["status"];
  pending: boolean;
}) {
  const semantic = statusSemantic(status, pending);
  const color =
    semantic === "pending"
      ? "bg-warn"
      : semantic === "failed"
        ? "bg-err"
        : semantic === "running"
          ? "bg-accent animate-pulse"
          : "bg-muted";
  return (
    <span
      className={`h-2 w-2 shrink-0 rounded-full ${color}`}
      title={`status: ${semantic}`}
    />
  );
}

function shortId(id: string) {
  return id.length <= 12 ? id : `${id.slice(0, 8)}…${id.slice(-4)}`;
}

function formatTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
