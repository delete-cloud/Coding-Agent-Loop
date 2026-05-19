Goal:
Implement deterministic test-failure evidence ingest/search fixtures for the context system.

Scope:
- Add a Coding Agent KB ingest path for bounded pytest failure evidence.
- Add a Coding Agent KB search path that returns ranked test-failure evidence with metadata.
- Cover ingest/search with local failure fixtures and deterministic fake embeddings.

Out of scope:
- Context pack data models or rendering.
- `build_context` injection changes.
- AgentKit runtime or pipeline changes.
- External LLM calls, external services, or production credentials.

Context:
- ADRs:
  - docs/adr/0034-context-system-boundaries-and-evidence.md
- Relevant files:
  - src/coding_agent/kb.py
  - tests/test_kb.py
  - tests/fixtures/context_system/
  - docs/context_system/GOAL_PROGRESS.md

Target tests:
- `uv run pytest tests/test_kb.py -k "failure_retrieval_indexes_pytest_failure_evidence or failure_retrieval_skips_non_failure_rows" -v`
- `uv run pytest tests/test_kb.py tests/coding_agent/test_kb_sync.py tests/cli/test_kb_commands.py -v`

Loop policy:
- Engineer implements the smallest correct change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate if the change needs context-pack rendering or `build_context` pipeline changes.
- Escalate if the change needs AgentKit runtime changes.
- Stop if deterministic local verification cannot be produced.
