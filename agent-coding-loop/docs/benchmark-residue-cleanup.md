# Benchmark Residue Cleanup

This checklist is for cleaning up stale benchmark branches, worktrees, and temporary repos after A/B runs.

## Long-lived branches and worktrees

Keep these by default:

- `main`
- the currently active feature branch/worktree you are using for development

Everything else should be treated as disposable unless it contains unmerged work.

## Typical residue patterns

Common benchmark leftovers in this repository look like:

- local branches named `agent-loop/<timestamp>`
- temporary worktrees under `/private/tmp/...`
- temporary worktrees under `/private/var/folders/.../ab_*`
- detached benchmark repos created by strict A/B runs

These are generated artifacts of benchmark execution, not long-term development state.

## Safe cleanup procedure

### 1. Inspect current worktrees

```bash
git worktree list
```

Before removing anything, confirm that the worktree:

- is not your current development worktree
- is tied to an already-merged or disposable benchmark branch
- does not contain uncommitted changes you want to keep

### 2. Remove prunable benchmark worktrees

Example:

```bash
git worktree remove /private/tmp/kb_mixed_debug --force
```

For temp benchmark repos under system temp folders, remove only entries clearly tied to old benchmark runs.

### 3. Delete stale local benchmark branches

List benchmark-style branches:

```bash
git branch --format='%(refname:short)' | rg '^agent-loop/'
```

Delete branches only after confirming they are obsolete:

```bash
git branch -D agent-loop/1772986060
```

### 4. Prune stale worktree metadata

```bash
git worktree prune
```

### 5. Verify repository is back to a clean baseline

```bash
git worktree list
git branch --format='%(refname:short)'
git status --short
```

## Rules

- Do not delete `main`
- Do not delete the currently active feature worktree
- Do not delete any branch/worktree that still contains unmerged work
- Treat benchmark temp repos as disposable, but verify before forcing removal
