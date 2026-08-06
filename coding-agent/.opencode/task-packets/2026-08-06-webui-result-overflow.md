Goal:
Keep the WebUI composer and timeline usable when an expanded Session result contains a long final answer. The result header must remain visible while the result body scrolls within a bounded portion of the viewport.

Scope:
- Bound the expanded result body height and make that body vertically scrollable.
- Ensure the composer remains a non-shrinking flex item.
- Add focused regression tests for both layout contracts.

Out of scope:
- Changing `/sessions/{id}/result`, display-event replay, checkpoint restore, or backend persistence.
- Removing the final answer from either Session result or Timeline.
- Changing ResultPanel's default expanded state, markdown rendering, or visual redesign.
- Dependency changes, generated bundle commits, deployment, or live o6n operations.

Context:
- Baseline: `origin/main` and `forgejo/main` at `0c7fc19aa3d2dea67880d3cdf9114feb190d84c7`.
- Root cause: expanded ResultPanel is `shrink-0` but its body is unbounded; in the `h-screen overflow-hidden` app shell a long answer compresses Timeline and pushes Composer content outside the visible layout.
- ADRs: none; this is a localized layout bug fix with no persistence, protocol, data-model, or module-boundary decision.
- Postmortem: no `related_files` match for the allowed implementation files as of the baseline.
- Relevant files:
  - `webui/app/src/components/ResultPanel.tsx`
  - `webui/app/src/components/ResultPanel.test.tsx`
  - `webui/app/src/components/Composer.tsx`
  - `webui/app/src/components/Composer.test.tsx` (may be added)

Acceptance Criteria:
1. An expanded long Session result has a bounded, vertically scrollable details region; its header stays outside that scrolling region.
2. Short results, markdown, verification summary, failure details, and collapse/expand behavior remain unchanged.
3. Composer's footer is explicitly non-shrinking in the main flex column.
4. Focused regression tests fail on the baseline implementation and pass after the fix.
5. The full frontend test suite, TypeScript no-emit check, and production build pass.
6. No files outside this packet's implementation/test scope and this packet itself are changed.

Constraints:
- Use existing Tailwind utility patterns; add no dependency and no thin wrapper.
- Prefer a viewport-relative cap so behavior remains useful across desktop and small screens.
- Keep the change localized and do not alter result/timeline data flow.
- Follow red-green order: add focused failing tests, record the failure, then implement.
- Edits only: do not commit, push, create/update PRs, call Forge APIs, deploy, run `kubectl`, use SSH, or invoke production endpoints.

Verification:
- `cd webui/app && ./node_modules/.bin/vitest run src/components/ResultPanel.test.tsx src/components/Composer.test.tsx`
- `cd webui/app && ./node_modules/.bin/vitest run`
- `cd webui/app && ./node_modules/.bin/tsc -b --noEmit`
- `cd webui/app && ./node_modules/.bin/tsc -b && ./node_modules/.bin/vite build`
- Inspect `git status --short`, `git diff --stat`, and the complete diff.

Handoff:
- Return the changed-file list, the exact red-test failure, post-fix verification commands/results, unresolved risks, and whether any task-packet constraint was not met.
- Expected implementation diff: ResultPanel body layout utilities, Composer footer shrink contract, and focused tests only.
- Remaining human/orchestrator work: independent diff verification, review gate, commit/push/PR/CI, then a separate merge/deployment authorization decision.

Loop policy:
- Engineer implements the smallest correct change and runs the focused tests.
- Reviewer reviews only the resulting diff and affected tests, reporting only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate architecture redirection, scope expansion, or inability to demonstrate red-green to the human.
- Ignore non-blocking optimization suggestions.
