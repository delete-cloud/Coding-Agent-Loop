import { useState, type ReactNode } from "react";

export type RailPanel = "diff" | "memory" | "checkpoints" | "settings" | null;

interface Props {
  panel: RailPanel;
  onToggle: (panel: Exclude<RailPanel, null>) => void;
  children: ReactNode;
}

const LABELS_KEY = "coding-agent-webui-rail-labels";

const readLabelsPref = () =>
  typeof localStorage !== "undefined" && localStorage.getItem(LABELS_KEY) === "1";
const writeLabelsPref = (expanded: boolean) => {
  if (typeof localStorage !== "undefined") {
    localStorage.setItem(LABELS_KEY, expanded ? "1" : "0");
  }
};

// Vertical icon strip plus one expandable panel. The strip itself can expand
// to show text labels next to the icons (persisted); below md the panel
// becomes an overlay drawer pinned left of the strip.
export default function RightRail({ panel, onToggle, children }: Props) {
  const [showLabels, setShowLabels] = useState(readLabelsPref);
  const toggleLabels = () => {
    setShowLabels((v) => {
      writeLabelsPref(!v);
      return !v;
    });
  };

  return (
    <>
      {panel && (
        <div
          className="fixed inset-0 z-30 bg-black/50 md:hidden"
          aria-hidden
          onClick={() => onToggle(panel)}
        />
      )}
      {panel && (
        <aside
          className={`fixed inset-y-0 z-40 flex w-[min(380px,calc(100vw-2.75rem))] flex-col border-l border-border bg-surface-1 md:static md:z-auto md:w-[380px] md:shrink-0 ${
            showLabels ? "right-32" : "right-11"
          }`}
          aria-label={`${panel} panel`}
        >
          {children}
        </aside>
      )}
      <nav
        className={`fixed inset-y-0 right-0 z-40 flex shrink-0 flex-col gap-1 border-l border-border bg-surface-1 py-2 md:static md:z-auto ${
          showLabels ? "w-32 items-stretch px-1.5" : "w-11 items-center"
        }`}
        aria-label="Workspace panels"
      >
        <RailButton
          label="diff"
          tooltip="Diff — workspace file changes"
          showLabel={showLabels}
          active={panel === "diff"}
          onClick={() => onToggle("diff")}
          icon={
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" className="h-4 w-4 shrink-0">
              <path d="M5 3.5h6" />
              <path d="M5 8h3.5" />
              <path d="M5 12.5h6" />
              <path d="M2 3.5h.01M2 8h.01M2 12.5h.01" strokeWidth="2.2" />
            </svg>
          }
        />
        <RailButton
          label="memory"
          tooltip="Memory — recall hits & review queue"
          showLabel={showLabels}
          active={panel === "memory"}
          onClick={() => onToggle("memory")}
          icon={
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.2" className="h-4 w-4 shrink-0">
              <ellipse cx="8" cy="4" rx="5" ry="2" />
              <path d="M3 4v8c0 1.1 2.2 2 5 2s5-.9 5-2V4" />
              <path d="M3 8c0 1.1 2.2 2 5 2s5-.9 5-2" />
            </svg>
          }
        />
        <RailButton
          label="checkpoints"
          tooltip="Checkpoints — snapshots & restore"
          showLabel={showLabels}
          active={panel === "checkpoints"}
          onClick={() => onToggle("checkpoints")}
          icon={
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4 shrink-0">
              <path d="M4 1.5h8v13l-4-3-4 3z" />
            </svg>
          }
        />
        <RailButton
          label="settings"
          tooltip="Settings — session runtime & codex accounts"
          showLabel={showLabels}
          active={panel === "settings"}
          onClick={() => onToggle("settings")}
          icon={
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" className="h-4 w-4 shrink-0">
              <circle cx="8" cy="8" r="2" />
              <path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M12.6 3.4l-1.4 1.4M4.8 11.2l-1.4 1.4" />
            </svg>
          }
        />
        <button
          type="button"
          className={`mt-auto flex h-8 items-center justify-center rounded-md text-muted transition-colors hover:bg-surface-2 hover:text-fg ${showLabels ? "" : "w-8"}`}
          aria-label={showLabels ? "Collapse panel labels" : "Expand panel labels"}
          aria-expanded={showLabels}
          title={showLabels ? "Hide labels" : "Show labels"}
          onClick={toggleLabels}
        >
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
            {showLabels ? <path d="M10 3l4 5-4 5M6 3L2 8l4 5" /> : <path d="M6 3l4 5-4 5M10 3l4 5-4 5" />}
          </svg>
        </button>
      </nav>
    </>
  );
}

function RailButton({
  label,
  tooltip,
  showLabel,
  active,
  onClick,
  icon,
}: {
  label: string;
  tooltip: string;
  showLabel: boolean;
  active: boolean;
  onClick: () => void;
  icon: ReactNode;
}) {
  return (
    <button
      type="button"
      className={`flex h-8 items-center rounded-md transition-colors ${
        showLabel ? "gap-2 px-2" : "w-8 justify-center"
      } ${
        active ? "bg-accent/15 text-accent" : "text-muted hover:bg-surface-2 hover:text-fg"
      }`}
      aria-label={`Toggle ${label} panel`}
      aria-pressed={active}
      title={tooltip}
      onClick={onClick}
    >
      {icon}
      {showLabel && <span className="truncate text-xs">{label}</span>}
    </button>
  );
}
