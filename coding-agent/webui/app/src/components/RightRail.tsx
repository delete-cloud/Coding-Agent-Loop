import type { ReactNode } from "react";

export type RailPanel = "diff" | "memory" | "checkpoints" | "settings" | null;

interface Props {
  panel: RailPanel;
  onToggle: (panel: Exclude<RailPanel, null>) => void;
  children: ReactNode;
}

// Vertical icon strip plus one expandable panel. Below md the panel becomes
// an overlay drawer pinned left of the icon strip; on md+ it expands inline.
export default function RightRail({ panel, onToggle, children }: Props) {
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
          className="fixed inset-y-0 right-11 z-40 flex w-[min(380px,calc(100vw-2.75rem))] flex-col border-l border-border bg-surface-1 md:static md:z-auto md:w-[380px] md:shrink-0"
          aria-label={`${panel} panel`}
        >
          {children}
        </aside>
      )}
      <nav
        className="fixed inset-y-0 right-0 z-40 flex w-11 shrink-0 flex-col items-center gap-1 border-l border-border bg-surface-1 py-2 md:static md:z-auto"
        aria-label="Workspace panels"
      >
        <RailButton
          label="diff"
          active={panel === "diff"}
          onClick={() => onToggle("diff")}
          icon={
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" className="h-4 w-4">
              <path d="M3 3.5h10" />
              <path d="M3 8h6.5" />
              <path d="M3 12.5h10" />
            </svg>
          }
        />
        <RailButton
          label="memory"
          active={panel === "memory"}
          onClick={() => onToggle("memory")}
          icon={
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.2" className="h-4 w-4">
              <ellipse cx="8" cy="4" rx="5" ry="2" />
              <path d="M3 4v8c0 1.1 2.2 2 5 2s5-.9 5-2V4" />
              <path d="M3 8c0 1.1 2.2 2 5 2s5-.9 5-2" />
            </svg>
          }
        />
        <RailButton
          label="checkpoints"
          active={panel === "checkpoints"}
          onClick={() => onToggle("checkpoints")}
          icon={
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
              <path d="M4 1.5h8v13l-4-3-4 3z" />
            </svg>
          }
        />
        <RailButton
          label="settings"
          active={panel === "settings"}
          onClick={() => onToggle("settings")}
          icon={
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" className="h-4 w-4">
              <circle cx="8" cy="8" r="2" />
              <path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M12.6 3.4l-1.4 1.4M4.8 11.2l-1.4 1.4" />
            </svg>
          }
        />
      </nav>
    </>
  );
}

function RailButton({
  label,
  active,
  onClick,
  icon,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
  icon: ReactNode;
}) {
  return (
    <button
      type="button"
      className={`flex h-8 w-8 items-center justify-center rounded-md transition-colors ${
        active ? "bg-accent/15 text-accent" : "text-muted hover:bg-surface-2 hover:text-fg"
      }`}
      aria-label={`Toggle ${label} panel`}
      aria-pressed={active}
      title={`${active ? "Close" : "Open"} ${label} panel`}
      onClick={onClick}
    >
      {icon}
    </button>
  );
}
