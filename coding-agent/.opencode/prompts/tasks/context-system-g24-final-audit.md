Goal:
Complete the Context System + Evaluation phase with an end-to-end smoke test, implementation report, durable baseline audit, and cleanup ledger updates.

Scope:
- Add a deterministic smoke test that exercises AgentKit `build_context` composition with Coding Agent KB retrieval, test-failure evidence, and memory reference context.
- Produce the final context-system implementation report.
- Audit ADR-0034 acceptance criteria against executable tests.
- Update the G12-G24 ledger with landed PR status and G24 verification evidence.

Out of scope:
- New retrieval algorithms, ranking changes, or production integrations.
- AgentKit runtime, `ContextBuilder`, or directive schema changes.
- External LLM calls, external judges, production credentials, or remote vector stores.

Context:
- ADR:
  - `docs/adr/0034-context-system-boundaries-and-evidence.md`
- Relevant files:
  - `tests/coding_agent/test_context_system_smoke.py`
  - `docs/context_system/IMPLEMENTATION_REPORT.md`
  - `docs/context_system/GOAL_PROGRESS.md`

Target tests:
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/test_kb.py -k "repo_chunk_metadata_records_source_kind_and_repo_path or repo_retrieval_returns_ranked_evidence_with_fake_embedder or failure_retrieval_indexes_pytest_failure_evidence" -v`
- `uv run pytest tests/coding_agent/evaluation/ -v`
- `uv run pytest tests/coding_agent/plugins/test_kb.py tests/coding_agent/plugins/test_kb_plugin.py tests/coding_agent/plugins/test_memory.py tests/coding_agent/test_context_pack.py tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/coding_agent/ -k "context_pack or retrieval or memory or evaluation" -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`

Loop policy:
- Engineer adds the final smoke test first.
- Engineer updates report/ledger only after smoke coverage is executable.
- Reviewer reports only P1/P2 issues.
- Engineer fixes accepted P1/P2 findings and reruns target tests.

Stop conditions:
- Stop if end-to-end smoke requires changing AgentKit runtime composition.
- Stop if verification requires external services or credentials.
- Stop if the final audit finds an unmet ADR-0034 acceptance criterion without an executable test.
- Stop after two failed fix iterations for the same failure.
