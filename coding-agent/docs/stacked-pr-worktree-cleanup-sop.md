# Stacked PR / Worktree Cleanup SOP

## Purpose

Use this SOP when several feature branches are developed as a stack in separate git worktrees and later need to be published, merged, and cleaned up safely.

## When to Use

- You created multiple task branches from a shared baseline.
- Downstream branches depend on upstream branches.
- You need to publish a clean PR stack and remove temporary worktrees afterward.

## Recommended Flow

1. Create one worktree per task.
2. Keep the base branch and downstream stack order explicit.
3. Commit each worktree independently.
4. Rebase downstream branches onto the committed upstream branch before publishing.
5. Push branches in stack order.
6. Open PRs with the correct base branch for each lane.
7. Merge in dependency order.
8. Remove the temporary worktrees after the stack is merged.

## Branch Ordering Rules

- Independent branches may base directly on `main`.
- A downstream branch must base on the branch it depends on, not on `main`.
- Do not mix upstream baseline changes into downstream PRs.
- If a downstream PR grows polluted, restack it before pushing.

## Verification Before Merge

- Run the branch-local tests that prove the branch’s own behavior.
- Re-run tests after any review fix.
- Confirm the PR diff is only the branch-local change set.
- Check that each PR’s `baseRefName` matches the intended dependency.

## Cleanup Checklist

- [ ] Merge or close the related PRs.
- [ ] Remove the temporary worktrees.
- [ ] Leave merged local branches only if you want them for history; otherwise delete them.
- [ ] Confirm `git worktree list` no longer shows the task worktrees.
- [ ] Confirm the repository root has no stray worktree state.

## Common Failure Modes

- **Wrong PR base** — downstream PR shows upstream changes again.
- **Duplicate baseline tests** — downstream PR re-implements proof already carried by the base branch.
- **Unrestacked worktree** — a branch still contains stale baseline commits after upstream lands.
- **Leaked temporary worktree** — merged worktree remains on disk after the PR is done.

## Practical Example

For a three-branch stack:

- `task/base-feature` → PR to `main`
- `task/downstream-feature` → PR to `task/base-feature`
- `task/independent-feature` → PR to `main`

Merge order:

1. base feature
2. downstream feature
3. independent feature

Then remove the task worktrees.
