import { useCallback, useEffect, useRef, useState } from "react";
import type { AgentClient } from "../lib/api";
import type { CheckpointMetadata } from "../lib/types";

interface Props {
  client: AgentClient;
  sessionId: string;
  // Called after a successful restore so the app can reload session state.
  onRestored: () => void | Promise<void>;
  // Called after a successful capture so the sidebar badge can refresh.
  onCaptured?: () => void;
}

// Checkpoint capture/restore panel
// (POST/GET /sessions/{id}/checkpoints, POST .../{checkpoint_id}/restore).
export default function CheckpointsPanel({ client, sessionId, onRestored, onCaptured }: Props) {
  const [checkpoints, setCheckpoints] = useState<CheckpointMetadata[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [label, setLabel] = useState("");
  const [creating, setCreating] = useState(false);
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [restoringId, setRestoringId] = useState<string | null>(null);
  const seqRef = useRef(0);

  const load = useCallback(async () => {
    const seq = ++seqRef.current;
    setLoading(true);
    setError(null);
    try {
      const list = await client.listCheckpoints(sessionId);
      if (seqRef.current !== seq) return;
      setCheckpoints(list);
      setLoading(false);
    } catch (e) {
      if (seqRef.current !== seq) return;
      setLoading(false);
      setError(`checkpoints load failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  }, [client, sessionId]);

  useEffect(() => {
    void load();
  }, [load]);

  const create = async () => {
    setCreating(true);
    setError(null);
    try {
      await client.captureCheckpoint(sessionId, label);
      setLabel("");
      await load();
      onCaptured?.();
    } catch (e) {
      setError(`checkpoint failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setCreating(false);
    }
  };

  const restore = async (checkpointId: string) => {
    setRestoringId(checkpointId);
    setError(null);
    try {
      await client.restoreCheckpoint(sessionId, checkpointId);
      setConfirmingId(null);
      await onRestored();
    } catch (e) {
      setConfirmingId(null);
      setError(`restore failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setRestoringId(null);
    }
  };

  return (
    <section className="flex h-full min-h-0 flex-col bg-surface-1">
      <div className="flex items-center justify-between border-b border-border px-4 py-2">
        <div className="text-sm font-semibold text-fg">
          Checkpoints · {checkpoints.length}
        </div>
        {error && <div className="text-xs text-err">{error}</div>}
      </div>
      <div className="flex items-center gap-2 border-b border-border px-4 py-2">
        <input
          className="min-w-0 flex-1 rounded-md border border-border bg-surface-0 px-2 py-1 text-xs text-fg placeholder:text-muted focus:border-accent focus:outline-none"
          placeholder="label (optional)"
          aria-label="checkpoint label"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
        />
        <button
          className="shrink-0 rounded-md border border-border px-2 py-1 text-xs text-fg transition-colors hover:border-border-active disabled:opacity-40"
          disabled={creating}
          onClick={() => void create()}
        >
          {creating ? "Creating…" : "Create checkpoint"}
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {loading ? (
          <div className="px-4 py-3 text-sm text-muted">Loading checkpoints…</div>
        ) : checkpoints.length === 0 ? (
          <div className="px-4 py-3 text-sm text-muted">No checkpoints</div>
        ) : (
          checkpoints.map((checkpoint) => (
            <div key={checkpoint.checkpoint_id} className="border-b border-border px-4 py-2 text-xs">
              <div className="flex items-center gap-2">
                <span
                  className="min-w-0 flex-1 truncate font-mono text-fg"
                  title={checkpoint.checkpoint_id}
                >
                  {checkpoint.label?.trim() || checkpoint.checkpoint_id}
                </span>
                <span className="shrink-0 text-muted">{formatTime(checkpoint.created_at)}</span>
                {confirmingId !== checkpoint.checkpoint_id && (
                  <button
                    className="shrink-0 rounded-md border border-border px-2 py-0.5 text-[11px] text-fg transition-colors hover:border-border-active disabled:opacity-40"
                    disabled={restoringId !== null}
                    onClick={() => setConfirmingId(checkpoint.checkpoint_id)}
                  >
                    Restore
                  </button>
                )}
              </div>
              <div className="mt-0.5 truncate font-mono text-muted">
                {checkpoint.checkpoint_id} · {checkpoint.entry_count}{" "}
                {checkpoint.entry_count === 1 ? "entry" : "entries"}
              </div>
              {confirmingId === checkpoint.checkpoint_id && (
                <div className="mt-2 rounded-md border border-warn/40 bg-warn/5 px-2 py-1.5">
                  <div className="text-[11px] text-warn">
                    Restore rewinds session history and runtime settings to this checkpoint.
                    Workspace files are not restored; inspect or save the workspace diff first.
                  </div>
                  <div className="mt-1.5 flex items-center gap-2">
                    <button
                      className="rounded-md border border-warn/40 px-2 py-0.5 text-[11px] font-medium text-warn transition-colors hover:bg-warn/10 disabled:opacity-40"
                      disabled={restoringId !== null}
                      onClick={() => void restore(checkpoint.checkpoint_id)}
                    >
                      {restoringId === checkpoint.checkpoint_id
                        ? "Restoring…"
                        : "Confirm restore"}
                    </button>
                    <button
                      className="rounded-md border border-border px-2 py-0.5 text-[11px] text-muted transition-colors hover:text-fg disabled:opacity-40"
                      disabled={restoringId !== null}
                      onClick={() => setConfirmingId(null)}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </section>
  );
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
