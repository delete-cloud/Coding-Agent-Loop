import { useCallback, useEffect, useRef, useState } from "react";
import type { AgentClient } from "../lib/api";
import type { MemoryReviewRecord } from "../lib/types";

type ReviewStatus = "candidate" | "accepted" | "rejected" | "archived";

const TABS: Array<{ id: ReviewStatus; label: string }> = [
  { id: "candidate", label: "Candidates" },
  { id: "accepted", label: "Accepted" },
  { id: "rejected", label: "Rejected" },
  { id: "archived", label: "Archived" },
];

interface Props {
  client: AgentClient;
  sessionId: string;
  // Called after a transition so the parent can refresh its accepted list.
  onChanged: () => void;
}

// Reviewed-memory curation queue (GET/POST /sessions/{id}/memory/reviews).
// Collapsed by default; the first expand loads candidates (or accepted when
// no candidates exist) so a passive panel open costs no extra requests.
export default function ReviewQueue({ client, sessionId, onChanged }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [tab, setTab] = useState<ReviewStatus>("candidate");
  const [records, setRecords] = useState<MemoryReviewRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rejectingId, setRejectingId] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const seqRef = useRef(0);
  const initializedRef = useRef(false);
  // Mirror of `tab` for async callbacks: a transition started on one tab must
  // reload whatever tab is current when its POST finishes, not the stale one.
  const tabRef = useRef<ReviewStatus>(tab);

  const load = useCallback(
    async (status: ReviewStatus): Promise<MemoryReviewRecord[] | null> => {
      const seq = ++seqRef.current;
      setLoading(true);
      setError(null);
      try {
        const list = await client.listMemoryReviews(sessionId, status);
        if (seqRef.current !== seq) return null;
        setRecords(list);
        setLoading(false);
        return list;
      } catch (e) {
        if (seqRef.current !== seq) return null;
        setRecords([]);
        setLoading(false);
        setError(`reviews load failed: ${e instanceof Error ? e.message : String(e)}`);
        return null;
      }
    },
    [client, sessionId],
  );

  // Initial load on first expand: candidates win; fall back to accepted when
  // none exist.
  useEffect(() => {
    if (!expanded || initializedRef.current) return;
    initializedRef.current = true;
    let cancelled = false;
    void (async () => {
      const candidates = await load("candidate");
      if (cancelled || candidates === null) return;
      if (candidates.length === 0) {
        tabRef.current = "accepted";
        setTab("accepted");
        await load("accepted");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [expanded, load]);

  const selectTab = (status: ReviewStatus) => {
    if (status === tab) return;
    tabRef.current = status;
    setTab(status);
    setRejectingId(null);
    setReason("");
    void load(status);
  };

  const transition = async (
    candidateId: string,
    status: "accepted" | "rejected",
    reasonText?: string,
  ) => {
    setBusyId(candidateId);
    setError(null);
    try {
      await client.transitionMemoryReview(sessionId, candidateId, status, reasonText);
      setRejectingId(null);
      setReason("");
      // Reload the tab that is current now — the user may have switched tabs
      // while the POST was in flight.
      await load(tabRef.current);
      onChanged();
    } catch (e) {
      setError(`review update failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div
      className={
        expanded
          ? "flex min-h-0 flex-1 flex-col overflow-auto border-b border-border"
          : "shrink-0 border-b border-border"
      }
    >
      <button
        type="button"
        className="flex w-full items-center gap-2 px-4 py-1.5 text-left text-xs font-semibold uppercase tracking-wide text-muted transition-colors hover:text-fg"
        aria-expanded={expanded}
        onClick={() => setExpanded((v) => !v)}
      >
        <span aria-hidden>{expanded ? "▾" : "▸"}</span>
        Review queue
      </button>
      {expanded && (
        <>
      <div
        className="flex items-center gap-1 border-b border-border px-3 py-1.5"
        role="tablist"
        aria-label="Memory review status"
      >
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            className={`rounded-md px-2 py-1 text-xs transition-colors ${
              tab === t.id
                ? "bg-accent/15 font-medium text-accent"
                : "text-muted hover:bg-surface-2 hover:text-fg"
            }`}
            onClick={() => selectTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      {error && (
        <div className="border-b border-err/30 bg-err/5 px-4 py-1.5 text-xs text-err">
          {error}
        </div>
      )}
      {loading ? (
        <div className="px-4 py-3 text-sm text-muted">Loading reviews…</div>
      ) : records.length === 0 ? (
        <div className="px-4 py-3 text-sm text-muted">
          No {TABS.find((t) => t.id === tab)?.label.toLowerCase()} memories
        </div>
      ) : (
        records.map((record) => (
          <ReviewRow
            key={record.candidate_id}
            record={record}
            actionable={tab === "candidate"}
            busy={busyId === record.candidate_id}
            rejecting={rejectingId === record.candidate_id}
            reason={rejectingId === record.candidate_id ? reason : ""}
            onReasonChange={setReason}
            onAccept={() => void transition(record.candidate_id, "accepted")}
            onRejectStart={() => {
              setRejectingId(record.candidate_id);
              setReason("");
            }}
            onRejectConfirm={() => void transition(record.candidate_id, "rejected", reason)}
            onRejectCancel={() => {
              setRejectingId(null);
              setReason("");
            }}
          />
        ))
      )}
        </>
      )}
    </div>
  );
}

function ReviewRow({
  record,
  actionable,
  busy,
  rejecting,
  reason,
  onReasonChange,
  onAccept,
  onRejectStart,
  onRejectConfirm,
  onRejectCancel,
}: {
  record: MemoryReviewRecord;
  actionable: boolean;
  busy: boolean;
  rejecting: boolean;
  reason: string;
  onReasonChange: (value: string) => void;
  onAccept: () => void;
  onRejectStart: () => void;
  onRejectConfirm: () => void;
  onRejectCancel: () => void;
}) {
  return (
    <div className="border-b border-border px-4 py-2 text-xs">
      <div className="flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate font-medium text-fg" title={record.title}>
          {record.title}
        </span>
        <span className="rounded-full border border-border px-1.5 py-0.5 text-accent">
          {record.kind}
        </span>
        <span className="text-muted">conf {record.confidence.toFixed(2)}</span>
      </div>
      <div className="mt-0.5 text-fg/75">{record.summary}</div>
      {record.review_reason && (
        <div className="mt-0.5 text-muted">reason: {record.review_reason}</div>
      )}
      {record.tags.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-1">
          {record.tags.map((tag) => (
            <span key={tag} className="rounded-full bg-surface-0 px-1.5 py-0.5 text-muted">
              {tag}
            </span>
          ))}
        </div>
      )}
      {actionable && !rejecting && (
        <div className="mt-2 flex items-center gap-2">
          <button
            className="rounded-md border border-ok/40 px-2 py-0.5 text-[11px] font-medium text-ok transition-colors hover:bg-ok/10 disabled:opacity-40"
            disabled={busy}
            onClick={onAccept}
          >
            Accept
          </button>
          <button
            className="rounded-md border border-err/40 px-2 py-0.5 text-[11px] font-medium text-err transition-colors hover:bg-err/10 disabled:opacity-40"
            disabled={busy}
            onClick={onRejectStart}
          >
            Reject
          </button>
        </div>
      )}
      {actionable && rejecting && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            className="min-w-32 flex-1 rounded-md border border-border bg-surface-0 px-2 py-1 text-xs text-fg placeholder:text-muted focus:border-accent focus:outline-none"
            placeholder="reason (optional)"
            aria-label="reject reason"
            value={reason}
            onChange={(e) => onReasonChange(e.target.value)}
          />
          <button
            className="rounded-md border border-err/40 px-2 py-0.5 text-[11px] font-medium text-err transition-colors hover:bg-err/10 disabled:opacity-40"
            disabled={busy}
            onClick={onRejectConfirm}
          >
            Confirm reject
          </button>
          <button
            className="rounded-md border border-border px-2 py-0.5 text-[11px] text-muted transition-colors hover:text-fg disabled:opacity-40"
            disabled={busy}
            onClick={onRejectCancel}
          >
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}
