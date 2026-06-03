interface Props {
  prompt: string;
  onPromptChange: (v: string) => void;
  onSend: () => void;
  onCancel: () => void;
  disabled: boolean;
  streaming: boolean;
}

export default function Composer({
  prompt,
  onPromptChange,
  onSend,
  onCancel,
  disabled,
  streaming,
}: Props) {
  return (
    <footer className="flex items-end gap-3 border-t border-border bg-surface-1 px-4 py-3">
      <textarea
        className="min-h-[48px] flex-1 resize-none rounded-xl border border-border bg-surface-0 px-4 py-2.5 text-sm text-fg placeholder:text-muted focus:border-accent focus:outline-none"
        placeholder="Ask the agent…  (Enter to send, Shift+Enter for newline)"
        value={prompt}
        onChange={(e) => onPromptChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            onSend();
          }
        }}
      />
      <button
        className="rounded-xl bg-accent px-5 py-2.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-40"
        disabled={disabled}
        onClick={onSend}
      >
        Send
      </button>
      <button
        className="rounded-xl border border-border px-5 py-2.5 text-sm text-fg transition-colors hover:border-border-active disabled:opacity-40"
        disabled={!streaming}
        onClick={onCancel}
      >
        Cancel
      </button>
    </footer>
  );
}
