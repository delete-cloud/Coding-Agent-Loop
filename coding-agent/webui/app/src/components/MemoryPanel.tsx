import type {
  ContextPack,
  ContextPackItem,
  MemoryReviewRecord,
  RuntimeRun,
} from "../lib/types";

interface Props {
  hits: ContextPackItem[];
  memories: MemoryReviewRecord[];
  loading: boolean;
  error: string | null;
}

// Flatten every run's metadata.context_pack sections into a single hit list.
// Defensive: runs without a context_pack (or with a malformed one) are skipped.
export function extractRecallHits(runs: RuntimeRun[]): ContextPackItem[] {
  const hits: ContextPackItem[] = [];
  for (const run of runs) {
    const pack = run.metadata?.context_pack as ContextPack | undefined;
    if (!pack || !Array.isArray(pack.sections)) continue;
    for (const section of pack.sections) {
      if (!section || !Array.isArray(section.items)) continue;
      for (const item of section.items) {
        if (item && typeof item === "object") hits.push(item);
      }
    }
  }
  return hits;
}

export default function MemoryPanel({ hits, memories, loading, error }: Props) {
  return (
    <section className="border-t border-border bg-surface-1">
      <div className="flex items-center justify-between border-b border-border px-4 py-2">
        <div className="text-sm font-semibold text-fg">
          Memory · {hits.length} recall {hits.length === 1 ? "hit" : "hits"} ·{" "}
          {memories.length} accepted
        </div>
        {error && <div className="text-xs text-err">{error}</div>}
      </div>
      <div className="grid max-h-80 overflow-hidden md:grid-cols-2">
        <div className="overflow-auto border-b border-border md:border-r md:border-b-0">
          <div className="border-b border-border px-4 py-1.5 text-xs font-semibold uppercase tracking-wide text-muted">
            Recall hits
          </div>
          {loading ? (
            <div className="px-4 py-3 text-sm text-muted">Loading memory…</div>
          ) : hits.length === 0 ? (
            <div className="px-4 py-3 text-sm text-muted">No recall hits</div>
          ) : (
            hits.map((hit, index) => (
              <div
                key={`${hit.source_kind}:${hit.source_id}:${index}`}
                className="border-b border-border px-4 py-2 text-xs"
              >
                <div className="flex items-center gap-2">
                  <span className="min-w-0 flex-1 truncate text-fg" title={hit.label}>
                    {hit.label}
                  </span>
                  <span className="text-muted">{formatScore(hit.score)}</span>
                  <span
                    className={`rounded-full border border-border px-1.5 py-0.5 ${scaleColor(hit.score_scale)}`}
                  >
                    {hit.score_scale ?? "unscored"}
                  </span>
                </div>
                <div className="mt-0.5 truncate text-muted">{hit.source_kind}</div>
              </div>
            ))
          )}
        </div>
        <div className="overflow-auto">
          <div className="border-b border-border px-4 py-1.5 text-xs font-semibold uppercase tracking-wide text-muted">
            Accepted memories
          </div>
          {loading ? (
            <div className="px-4 py-3 text-sm text-muted">Loading memory…</div>
          ) : memories.length === 0 ? (
            <div className="px-4 py-3 text-sm text-muted">No accepted memories</div>
          ) : (
            memories.map((memory) => (
              <div
                key={memory.candidate_id}
                className="border-b border-border px-4 py-2 text-xs"
              >
                <div className="flex items-center gap-2">
                  <span className="min-w-0 flex-1 truncate font-medium text-fg" title={memory.title}>
                    {memory.title}
                  </span>
                  <span className="rounded-full border border-border px-1.5 py-0.5 text-accent">
                    {memory.kind}
                  </span>
                  <span className="text-muted">conf {memory.confidence.toFixed(2)}</span>
                </div>
                <div className="mt-0.5 text-fg/75">{memory.summary}</div>
                {memory.tags.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {memory.tags.map((tag) => (
                      <span
                        key={tag}
                        className="rounded-full bg-surface-0 px-1.5 py-0.5 text-muted"
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      </div>
    </section>
  );
}

function formatScore(score: number | null | undefined) {
  return typeof score === "number" ? score.toFixed(2) : "—";
}

function scaleColor(scale: string | null | undefined) {
  if (scale === "similarity") return "text-accent";
  if (scale === "overlap") return "text-warn";
  return "text-muted";
}
