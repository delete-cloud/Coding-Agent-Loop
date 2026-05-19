Goal:
Add repo-aware retrieval query behavior with deterministic fixtures.

Scope:
- Add a Coding Agent KB retrieval API that returns ranked repo evidence from G14 chunk metadata.
- Keep legacy KB rows without repo metadata loadable and non-fatal during repo retrieval.
- Add deterministic fake-embedder tests for repo indexing plus ranked evidence retrieval.
- Update `docs/context_system/GOAL_PROGRESS.md` with G15 before/after evidence.

Out of scope:
- Context-pack rendering or injection.
- Testing-failure retrieval.
- Retrieval observability spans.
- Memory evidence persistence.
- AgentKit runtime or pipeline changes.

Context:
- ADRs:
  - `docs/adr/0034-context-system-boundaries-and-evidence.md`
- Postmortems:
  - `postmortem/patterns/PM-0009-preserve-neutral-bare-anchor-semantics.md`
- Relevant files:
  - `src/coding_agent/kb.py`
  - `tests/test_kb.py`
  - `tests/coding_agent/test_kb_sync.py`
  - `tests/cli/test_kb_commands.py`
  - `docs/context_system/GOAL_PROGRESS.md`

Target tests:
- `uv run pytest tests/test_kb.py -k "repo_retrieval_returns_ranked_evidence_with_fake_embedder or repo_retrieval_skips_legacy_rows_without_repo_metadata" -v`
- `uv run pytest tests/test_kb.py tests/coding_agent/test_kb_sync.py tests/cli/test_kb_commands.py -v`

Loop policy:
- Engineer writes failing tests first, then implements the smallest correct change.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- More than two fix iterations fail for the same reason.
- The change requires context-pack rendering or `build_context` pipeline changes.
- The change requires AgentKit runtime changes.
- The change requires external services or production credentials.
