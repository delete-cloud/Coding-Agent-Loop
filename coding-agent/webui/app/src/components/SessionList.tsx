import type { SessionSummary } from "../lib/types";

interface Props {
  sessions: SessionSummary[];
  activeSessionId: string | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
  onSelect: (sessionId: string) => void;
}

export default function SessionList({
  sessions,
  activeSessionId,
  loading,
  error,
  onRefresh,
  onSelect,
}: Props) {
  return (
    <aside className="flex w-full flex-col border-r border-border bg-surface-1 md:w-72">
      <div className="flex items-center justify-between border-b border-border px-3 py-2.5">
        <div className="text-xs font-semibold tracking-wide text-muted uppercase">
          Sessions
        </div>
        <button
          className="rounded-md border border-border px-2 py-1 text-xs text-fg transition-colors hover:border-border-active disabled:opacity-40"
          disabled={loading}
          onClick={onRefresh}
        >
          Refresh
        </button>
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
}: {
  session: SessionSummary;
  active: boolean;
  onSelect: (sessionId: string) => void;
}) {
  return (
    <button
      className={`block w-full border-b border-border px-3 py-3 text-left transition-colors hover:bg-surface-2 ${
        active ? "bg-accent/10" : ""
      }`}
      onClick={() => onSelect(session.session_id)}
    >
      <div className="flex items-center gap-2">
        <StatusDot status={session.status} pending={session.pending_approval} />
        <span className="truncate text-sm font-medium text-fg">
          {shortId(session.session_id)}
        </span>
        {session.turn_in_progress && (
          <span className="rounded bg-warn/10 px-1.5 py-0.5 text-[10px] font-medium text-warn">
            running
          </span>
        )}
      </div>
      <div className="mt-1 truncate text-xs text-muted">
        {session.model_name ?? "model ?"}
      </div>
      <div className="mt-1 text-[11px] text-muted">
        {formatTime(session.last_activity)}
      </div>
    </button>
  );
}

function StatusDot({
  status,
  pending,
}: {
  status: SessionSummary["status"];
  pending: boolean;
}) {
  const color = pending
    ? "bg-warn"
    : status === "failed"
      ? "bg-err"
      : status === "running"
        ? "bg-accent"
        : status === "completed"
          ? "bg-ok"
          : "bg-muted";
  return <span className={`h-2 w-2 shrink-0 rounded-full ${color}`} />;
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
