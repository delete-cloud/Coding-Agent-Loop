import type { WorkspaceDiff, WorkspacePatch } from "../lib/types";

interface Props {
  diff: WorkspaceDiff;
  patch: WorkspacePatch | null;
  onClose: () => void;
}

export default function DiffPanel({ diff, patch, onClose }: Props) {
  return (
    <section className="border-t border-border bg-surface-1">
      <div className="flex items-center justify-between border-b border-border px-4 py-2">
        <div className="text-sm font-semibold text-fg">
          Workspace diff · +{diff.additions} -{diff.deletions}
        </div>
        <button
          className="rounded-md border border-border px-2 py-1 text-xs text-fg transition-colors hover:border-border-active"
          onClick={onClose}
        >
          Close
        </button>
      </div>
      <div className="grid max-h-80 overflow-hidden md:grid-cols-[18rem_minmax(0,1fr)]">
        <div className="overflow-auto border-b border-border md:border-r md:border-b-0">
          {diff.files.length === 0 ? (
            <div className="px-4 py-3 text-sm text-muted">No changes</div>
          ) : (
            diff.files.map((file) => (
              <div
                key={`${file.old_path ?? ""}:${file.path}`}
                className="grid grid-cols-[4.5rem_1fr] gap-2 border-b border-border px-4 py-2 text-xs"
              >
                <span className={statusColor(file.status)}>{file.status}</span>
                <span className="truncate text-fg" title={file.path}>
                  {file.path}
                </span>
                <span className="text-muted">
                  +{file.additions ?? 0} -{file.deletions ?? 0}
                </span>
                <span className="truncate text-muted">
                  {file.old_path ? `from ${file.old_path}` : file.binary ? "binary" : ""}
                </span>
              </div>
            ))
          )}
        </div>
        <pre className="overflow-auto bg-surface-0 px-4 py-3 text-xs leading-relaxed">
          {patch?.patch
            ? patch.patch.split("\n").map((line, index) => (
                <div key={`${index}:${line}`} className={lineColor(line)}>
                  {line || " "}
                </div>
              ))
            : <span className="text-muted">No unified patch available</span>}
        </pre>
      </div>
    </section>
  );
}

function statusColor(status: string) {
  if (status === "added") return "font-medium text-ok";
  if (status === "deleted") return "font-medium text-err";
  if (status === "modified" || status === "renamed") return "font-medium text-warn";
  return "font-medium text-muted";
}

function lineColor(line: string) {
  if (line.startsWith("+") && !line.startsWith("+++")) return "text-ok";
  if (line.startsWith("-") && !line.startsWith("---")) return "text-err";
  if (line.startsWith("@@")) return "text-accent";
  if (line.startsWith("diff --git") || line.startsWith("index ")) return "text-warn";
  return "text-fg/75";
}
