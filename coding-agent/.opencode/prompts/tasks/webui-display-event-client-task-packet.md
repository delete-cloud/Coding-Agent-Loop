Goal:
Bring the standalone webui source into the tracked repository and make both the
React app and no-build HTML client consume the user-facing `DisplayEvent`
stream instead of legacy wire SSE events.

Scope:
- Track only webui source, manifests, and lockfile; exclude dependency/build
  artifacts.
- Request `event_format=display` for prompt streams.
- Render timeline/status from `DisplayEvent.display_kind` and payload data.
- Keep the legacy HTTP wire stream unchanged server-side.
- Update ADR-0058 to mark standalone webui DisplayEvent integration complete.

Out of scope:
- Tracking `node_modules`, Vite caches, or TypeScript build metadata.
- Reworking visual design or adding new product workflows.
- Changing backend API contracts.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Relevant files:
  - `webui/README.md`
  - `webui/index.html`
  - `webui/app/src/lib/api.ts`
  - `webui/app/src/lib/types.ts`
  - `webui/app/src/lib/timeline.ts`
  - `webui/app/src/App.tsx`

Target tests:
- `pnpm --dir webui/app install --frozen-lockfile`
- `pnpm --dir webui/app typecheck`
- `pnpm --dir webui/app build`
- `git diff --check`

Loop policy:
- Engineer implements the smallest correct change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Stop if the source-only import requires tracking dependency/build artifacts.
- Ignore non-blocking visual polish suggestions.
