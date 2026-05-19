# Context System Goal Progress

Date started: 2026-05-19
Baseline: Durable runtime G00-G11 is complete on `main`.

This file is the phase ledger for G12-G24. Before each goal, append the goal id, intended files, verification commands, and stop criteria. After each goal, append changed files, tests run, results, and remaining risks.

## Phase Constraints

- Keep AgentKit Core generic.
- Use AgentKit `build_context` hooks for context injection.
- Do not rewrite the AgentKit pipeline.
- Preserve JSONL compatibility.
- Do not break durable runtime tests from G00-G11.
- Do not require real external LLM calls, production credentials, or external services for tests.
- Do not implement schedules, desktop, bridge, sandbox, or autonomous proactive-agent work in this phase.
- Treat memory as reference context with evidence, not instructions.
- Do not add raw prompt, content, message, result, secret, or text values to trace attributes.

## Goal Map

No pre-existing repository document defined G12-G24 individually. The following map decomposes the requested phase into sequential, reviewable goals.

| Goal | Scope |
| --- | --- |
| G12 | Current-state audit, phase goal map, and task-packet/ledger setup. |
| G13 | ADR for context-system boundaries, context packs, retrieval observability, and memory evidence semantics. |
| G14 | Repo-aware retrieval source and chunk metadata model. |
| G15 | Repo-aware retrieval indexing/query behavior with deterministic fixtures. |
| G16 | Testing failure retrieval ingest/search with local failure fixtures. |
| G17 | Context pack data model and renderer contract. |
| G18 | Context pack injection through existing `build_context` hooks. |
| G19 | Safe retrieval observability counters and spans. |
| G20 | Manifest-driven deterministic evaluation harness baseline. |
| G21 | Evaluation golden cases for retrieval and context-pack behavior. |
| G22 | Memory records with evidence references and JSONL-compatible migration behavior. |
| G23 | Memory retrieval/injection as reference context with evidence, not instructions. |
| G24 | End-to-end context-system smoke, implementation report, durable baseline audit, and cleanup. |

## G12 - Current-State Audit And Phase Ledger

Status: passed local verification.

### Before

Goal id: G12

Intended files:

- `docs/context_system/GOAL_PROGRESS.md`
- `docs/context_system/CURRENT_STATE.md`
- `.opencode/prompts/tasks/context-system-g12-current-state.md`

Verification commands:

- `test -f docs/context_system/GOAL_PROGRESS.md`
- `test -f docs/context_system/CURRENT_STATE.md`
- `test -f .opencode/prompts/tasks/context-system-g12-current-state.md`
- `uv run pytest tests/coding_agent/plugins/test_kb.py tests/coding_agent/plugins/test_kb_plugin.py tests/coding_agent/plugins/test_memory.py tests/coding_agent/evaluation/test_adapter.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`

Stop criteria:

- Cannot identify current KB/RAG/Memory/build_context/evaluation entrypoints.
- Documentation requires changing production runtime semantics.
- Deterministic verification cannot be produced.
- More than two fix iterations fail for the same reason.

Postmortem routing:

- Intended G12 files do not match any `postmortem/index.yaml` `related_files` entry.

### After

Changed files:

- `docs/context_system/GOAL_PROGRESS.md`
- `docs/context_system/CURRENT_STATE.md`
- `.opencode/prompts/tasks/context-system-g12-current-state.md`

Tests run:

- `test -f docs/context_system/GOAL_PROGRESS.md`
- `test -f docs/context_system/CURRENT_STATE.md`
- `test -f .opencode/prompts/tasks/context-system-g12-current-state.md`
- `uv run pytest tests/coding_agent/plugins/test_kb.py tests/coding_agent/plugins/test_kb_plugin.py tests/coding_agent/plugins/test_memory.py tests/coding_agent/evaluation/test_adapter.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`

Results:

- File existence checks passed.
- KB, KBPlugin, MemoryPlugin, and evaluation adapter tests passed: 40 passed.
- AgentKit build_context/runtime-stage span tests passed: 8 passed, 29 deselected.

Remaining risks:

- G12 is docs-only and does not prove the future retrieval design.
- The G12-G24 goal map is inferred from the phase objective because no prior repository document defined those individual goal ids.

## G13 - Context-System Boundary ADR

Status: passed local verification.

### Before

Goal id: G13

Intended files:

- `docs/adr/0034-context-system-boundaries-and-evidence.md`
- `docs/context_system/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/context-system-g13-adr.md`

Verification commands:

- `test -f docs/adr/0034-context-system-boundaries-and-evidence.md`
- `test -f .opencode/prompts/tasks/context-system-g13-adr.md`
- `rg -n "## Context|## Decision|## Alternatives Rejected|## Acceptance Criteria|## References" docs/adr/0034-context-system-boundaries-and-evidence.md`
- `uv run pytest tests/coding_agent/plugins/test_kb.py tests/coding_agent/plugins/test_kb_plugin.py tests/coding_agent/plugins/test_memory.py tests/coding_agent/evaluation/test_adapter.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`

Stop criteria:

- ADR would require rewriting AgentKit pipeline behavior.
- ADR would require durable runtime semantic changes.
- Deterministic verification cannot be produced.
- More than two fix iterations fail for the same reason.

Postmortem routing:

- Intended G13 files do not match any `postmortem/index.yaml` `related_files` entry.

### After

Changed files:

- `docs/adr/0034-context-system-boundaries-and-evidence.md`
- `docs/context_system/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/context-system-g13-adr.md`

Tests run:

- `test -f docs/adr/0034-context-system-boundaries-and-evidence.md`
- `test -f .opencode/prompts/tasks/context-system-g13-adr.md`
- `rg -n "## Context|## Decision|## Alternatives Rejected|## Acceptance Criteria|## References" docs/adr/0034-context-system-boundaries-and-evidence.md`
- `uv run pytest tests/coding_agent/plugins/test_kb.py tests/coding_agent/plugins/test_kb_plugin.py tests/coding_agent/plugins/test_memory.py tests/coding_agent/evaluation/test_adapter.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`

Results:

- ADR file and G13 task packet existence checks passed.
- ADR required-section check passed.
- KB, KBPlugin, MemoryPlugin, and evaluation adapter tests passed: 40 passed.
- AgentKit build_context/runtime-stage span tests passed: 8 passed, 29 deselected.

Remaining risks:

- ADR acceptance criteria intentionally describe future G14-G24 implementation tests that do not exist yet.
- Context-pack shape is still conceptual until G17 introduces executable model tests.

## G14 - Repo-Aware Retrieval Source And Chunk Metadata

Status: passed local verification.

### Before

Goal id: G14

Intended files:

- `src/coding_agent/kb.py`
- `tests/test_kb.py`
- `docs/context_system/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/context-system-g14-repo-metadata.md`

Verification commands:

- `uv run pytest tests/test_kb.py -k "repo_chunk_metadata_records_source_kind_and_repo_path or index_directory_records_repo_relative_metadata or index_file_rejects_path_outside_repo_root" -v`
- `uv run pytest tests/test_kb.py tests/coding_agent/test_kb_sync.py tests/cli/test_kb_commands.py -v`
- `uv run pytest tests/coding_agent/plugins/test_kb.py tests/coding_agent/plugins/test_kb_plugin.py -v`

Stop criteria:

- Change requires rewriting AgentKit pipeline behavior.
- Change requires durable runtime semantic changes.
- Deterministic verification cannot be produced.
- More than two fix iterations fail for the same reason.

Postmortem routing:

- `src/coding_agent/kb.py` matches PM-0009. Release checks: run focused tests for affected KB behavior and review affected control-flow shape before shipping.

### After

Changed files:

- `src/coding_agent/kb.py`
- `tests/test_kb.py`
- `docs/context_system/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/context-system-g14-repo-metadata.md`

Tests run:

- Red: `uv run pytest tests/test_kb.py -k "repo_chunk_metadata_records_source_kind_and_repo_path or index_directory_records_repo_relative_metadata or index_file_rejects_path_outside_repo_root" -v`
- Green: `uv run pytest tests/test_kb.py -k "repo_chunk_metadata_records_source_kind_and_repo_path or index_directory_records_repo_relative_metadata or index_file_rejects_path_outside_repo_root" -v`
- CodeRabbit fix red: `uv run pytest tests/test_kb.py -k "embedding_count_mismatch" -v`
- CodeRabbit fix green: `uv run pytest tests/test_kb.py -k "embedding_count_mismatch" -v`
- Local review fix red: `uv run pytest tests/test_kb.py -k "repo_source_id_stays_stable_when_document_content_changes or index_directory_records_symlink_repo_path or index_directory_skips_symlink_targets_outside_repo" -v`
- Local review fix green: `uv run pytest tests/test_kb.py -k "repo_source_id_stays_stable_when_document_content_changes or index_directory_records_symlink_repo_path or index_directory_skips_symlink_targets_outside_repo" -v`
- `uv run ruff format --check src/coding_agent/kb.py tests/test_kb.py`
- `uv run pytest tests/test_kb.py -k "repo_chunk_metadata_records_source_kind_and_repo_path or index_directory_records_repo_relative_metadata or index_file_rejects_path_outside_repo_root or embedding_count_mismatch or repo_source_id_stays_stable_when_document_content_changes or index_directory_records_symlink_repo_path or index_directory_skips_symlink_targets_outside_repo" -v`
- `uv run pytest tests/test_kb.py tests/coding_agent/test_kb_sync.py tests/cli/test_kb_commands.py -v`
- `uv run pytest tests/coding_agent/plugins/test_kb.py tests/coding_agent/plugins/test_kb_plugin.py -v`

Results:

- Red test failed for missing `repo_root` support and missing repo-aware metadata.
- Focused repo metadata tests passed after implementation: 7 passed, 29 deselected.
- CodeRabbit regression test failed before `zip(..., strict=True)` and passed after the fix: 1 passed, 32 deselected.
- Local review regression tests failed before stable `source_id` and symlink path handling fixes, then passed: 3 passed, 33 deselected.
- Ruff format check passed after formatting touched Python files.
- KB, sync, and CLI KB tests passed: 46 passed.
- KB plugin tests passed: 15 passed.

Remaining risks:

- G14 records metadata but does not change retrieval ranking or context rendering yet.
- Existing LanceDB rows created before G14 do not have the new metadata keys; future code must tolerate legacy metadata during retrieval.

## G15 - Repo-Aware Retrieval Indexing And Query Fixtures

Status: passed local verification.

### Before

Goal id: G15

Intended files:

- `src/coding_agent/kb.py`
- `tests/test_kb.py`
- `docs/context_system/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/context-system-g15-retrieval-fixtures.md`

Verification commands:

- `uv run pytest tests/test_kb.py -k "repo_retrieval_returns_ranked_evidence_with_fake_embedder or repo_retrieval_skips_legacy_rows_without_repo_metadata" -v`
- `uv run pytest tests/test_kb.py tests/coding_agent/test_kb_sync.py tests/cli/test_kb_commands.py -v`

Stop criteria:

- Change requires context-pack rendering or `build_context` pipeline changes.
- Change requires AgentKit runtime changes.
- Deterministic verification cannot be produced.
- More than two fix iterations fail for the same reason.

Postmortem routing:

- `src/coding_agent/kb.py` matches PM-0009. Release checks: run focused tests for affected KB behavior and review affected control-flow shape before shipping.

### After

Changed files:

- `src/coding_agent/kb.py`
- `tests/test_kb.py`
- `docs/context_system/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/context-system-g15-retrieval-fixtures.md`

Tests run:

- Red: `uv run pytest tests/test_kb.py -k "repo_retrieval_returns_ranked_evidence_with_fake_embedder or repo_retrieval_skips_legacy_rows_without_repo_metadata" -v`
- Green: `uv run pytest tests/test_kb.py -k "repo_retrieval_returns_ranked_evidence_with_fake_embedder or repo_retrieval_skips_legacy_rows_without_repo_metadata" -v`
- Local review fix red: `uv run pytest tests/test_kb.py -k "repo_retrieval_expands_past_legacy_candidate_window" -v`
- Local review fix green: `uv run pytest tests/test_kb.py -k "repo_retrieval_expands_past_legacy_candidate_window" -v`
- CodeRabbit fix red: `uv run pytest tests/test_kb.py -k "repo_retrieval_stops_at_candidate_fetch_cap" -v`
- CodeRabbit fix green: `uv run pytest tests/test_kb.py -k "repo_retrieval_stops_at_candidate_fetch_cap" -v`
- Local review gate fix red: `uv run pytest tests/test_kb.py -k "repo_retrieval_rejects_k_above_candidate_fetch_cap" -v`
- Local review gate fix green: `uv run pytest tests/test_kb.py -k "repo_retrieval_rejects_k_above_candidate_fetch_cap" -v`
- `uv run ruff format --check src/coding_agent/kb.py tests/test_kb.py`
- `uv run pytest tests/test_kb.py tests/coding_agent/test_kb_sync.py tests/cli/test_kb_commands.py -v`

Results:

- Red tests failed before `KB.search_repo` existed.
- Focused repo retrieval tests passed after implementation: 2 passed, 36 deselected.
- Local review regression failed before expanding past legacy candidate windows and passed after iterative candidate expansion: 1 passed, 38 deselected.
- CodeRabbit regression failed before capping adaptive fetch growth and passed after adding `_MAX_REPO_RETRIEVAL_FETCH_K`: 1 passed, 39 deselected.
- Local review gate regression failed before rejecting `k` above the fetch cap and passed after input validation moved ahead of table lookup: 1 passed, 40 deselected.
- Ruff format check passed.
- KB, sync, and CLI KB tests passed: 51 passed.

Remaining risks:

- G15 returns repo evidence from KB but does not render context packs or inject through `build_context`; those remain G17/G18.
- G15 skips legacy rows without repo metadata for repo retrieval; generic vector search still returns those rows unchanged.

## G16 - Testing Failure Retrieval Ingest/Search Fixtures

Status: passed local verification.

### Before

Goal id: G16

Intended files:

- `src/coding_agent/kb.py`
- `tests/test_kb.py`
- `tests/fixtures/context_system/pytest_auth_failure.txt`
- `tests/fixtures/context_system/pytest_billing_failure.txt`
- `docs/context_system/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/context-system-g16-failure-retrieval-fixtures.md`

Verification commands:

- `uv run pytest tests/test_kb.py -k "failure_retrieval_indexes_pytest_failure_evidence or failure_retrieval_skips_non_failure_rows" -v`
- `uv run pytest tests/test_kb.py tests/coding_agent/test_kb_sync.py tests/cli/test_kb_commands.py -v`

Stop criteria:

- Change requires context-pack rendering or `build_context` pipeline changes.
- Change requires AgentKit runtime changes.
- Deterministic verification cannot be produced.
- More than two fix iterations fail for the same reason.

Postmortem routing:

- `src/coding_agent/kb.py` matches PM-0009. Release checks: run focused tests for affected KB behavior and review affected control-flow shape before shipping.

### After

Changed files:

- `src/coding_agent/kb.py`
- `tests/test_kb.py`
- `tests/fixtures/context_system/pytest_auth_failure.txt`
- `tests/fixtures/context_system/pytest_billing_failure.txt`
- `docs/context_system/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/context-system-g16-failure-retrieval-fixtures.md`

Tests run:

- Red: `uv run pytest tests/test_kb.py -k "failure_retrieval_indexes_pytest_failure_evidence or failure_retrieval_skips_non_failure_rows" -v`
- Green: `uv run pytest tests/test_kb.py -k "failure_retrieval_indexes_pytest_failure_evidence or failure_retrieval_skips_non_failure_rows" -v`
- Local review fix red: `uv run pytest tests/test_kb.py -k "failure_retrieval_upserts_same_failure_evidence" -v`
- Local review fix green: `uv run pytest tests/test_kb.py -k "failure_retrieval_upserts_same_failure_evidence" -v`
- `uv run ruff format --check src/coding_agent/kb.py tests/test_kb.py`
- `git diff --check -- .`
- `uv run pytest tests/test_kb.py tests/coding_agent/test_kb_sync.py tests/cli/test_kb_commands.py -v`
- `uv run pytest tests/coding_agent/plugins/test_kb.py tests/coding_agent/plugins/test_kb_plugin.py -v`

Results:

- Red tests failed before `KB.index_test_failure` existed.
- Focused failure retrieval tests passed after implementation: 2 passed, 41 deselected.
- Local review regression failed before deterministic failure evidence upsert and passed after switching failure ingest to `merge_insert`: 1 passed, 43 deselected.
- Ruff format check passed.
- Git diff whitespace check passed.
- KB, sync, and CLI KB tests passed: 54 passed.
- KB plugin tests passed: 15 passed.

Remaining risks:

- G16 indexes and retrieves test-failure evidence but does not render context packs or inject through `build_context`; those remain G17/G18.
- G16 stores bounded failure snippets for retrieval; richer pytest output parsing and automatic command-log ingestion are deferred unless future goals require them.

## G17 - Context Pack Data Model And Renderer Contract

Status: passed local verification.

### Before

Goal id: G17

Intended files:

- `src/coding_agent/context_pack.py`
- `tests/coding_agent/test_context_pack.py`
- `docs/context_system/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/context-system-g17-context-pack-model.md`

Verification commands:

- `uv run pytest tests/coding_agent/test_context_pack.py -v`
- `uv run pytest tests/coding_agent/ -k "context_pack or retrieval or memory or evaluation" -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context" -v`

Stop criteria:

- Change requires `build_context` plugin wiring.
- Change requires AgentKit runtime changes.
- Deterministic verification cannot be produced.
- More than two fix iterations fail for the same reason.

Postmortem routing:

- Intended G17 production file is new and does not match any `postmortem/index.yaml` `related_files` entry.

### After

Changed files:

- `src/coding_agent/context_pack.py`
- `tests/coding_agent/test_context_pack.py`
- `docs/context_system/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/context-system-g17-context-pack-model.md`

Tests run:

- Red: `uv run pytest tests/coding_agent/test_context_pack.py -v`
- Green: `uv run pytest tests/coding_agent/test_context_pack.py -v`
- `uv run ruff format --check src/coding_agent/context_pack.py tests/coding_agent/test_context_pack.py`
- `git diff --check -- .`
- `uv run pytest tests/coding_agent/ -k "context_pack or retrieval or memory or evaluation" -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context" -v`

Results:

- Red test failed before `coding_agent.context_pack` existed.
- Focused context pack tests passed after implementation: 4 passed.
- Ruff format check passed after formatting the test file.
- Git diff whitespace check passed.
- Coding Agent context/memory/evaluation filtered tests passed: 33 passed, 586 deselected.
- AgentKit build_context regression tests passed: 7 passed, 30 deselected.

Remaining risks:

- G17 defines and renders context packs but does not inject them through plugin `build_context`; that remains G18.
- G17 renders memory as evidence-backed reference material and omits unevidenced memory by default, but persisted memory evidence fields are not added until G22.

## G18 - Context Pack Injection Through Existing Build Context Hooks

Status: passed local verification.

### Before

Goal id: G18

Intended files:

- `src/coding_agent/plugins/kb.py`
- `tests/coding_agent/plugins/test_kb.py`
- `tests/coding_agent/plugins/test_kb_plugin.py`
- `docs/context_system/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/context-system-g18-context-pack-injection.md`

Verification commands:

- `uv run pytest tests/coding_agent/plugins/test_kb.py tests/coding_agent/plugins/test_kb_plugin.py -v`
- `uv run pytest tests/coding_agent/ -k "context_pack or retrieval or memory or evaluation" -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context" -v`

Stop criteria:

- Change requires AgentKit runtime or pipeline changes.
- Change requires memory persistence format changes.
- Deterministic verification cannot be produced.
- More than two fix iterations fail for the same reason.

Postmortem routing:

- `src/coding_agent/plugins/kb.py` matches PM-0009 and PM-0017. Release checks: run focused tests for affected plugin/bootstrap behavior and review affected control-flow shape before shipping.

### After

Changed files:

- `src/coding_agent/plugins/kb.py`
- `tests/coding_agent/plugins/test_kb.py`
- `tests/coding_agent/plugins/test_kb_plugin.py`
- `docs/context_system/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/context-system-g18-context-pack-injection.md`

Tests run:

- Red: `uv run pytest tests/coding_agent/plugins/test_kb.py tests/coding_agent/plugins/test_kb_plugin.py -v`
- Green: `uv run pytest tests/coding_agent/plugins/test_kb.py tests/coding_agent/plugins/test_kb_plugin.py -v`
- `uv run ruff format --check src/coding_agent/plugins/kb.py tests/coding_agent/plugins/test_kb.py tests/coding_agent/plugins/test_kb_plugin.py`
- `git diff --check -- .`
- `uv run pytest tests/coding_agent/ -k "context_pack or retrieval or memory or evaluation" -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context" -v`

Results:

- Red tests failed while `KBPlugin.build_context` still emitted legacy `[KB]` grounding.
- Focused KB plugin tests passed after rendering a context pack through the existing `build_context` hook: 16 passed.
- Ruff format check passed after formatting changed plugin test files.
- Git diff whitespace check passed.
- Coding Agent context/memory/evaluation filtered tests passed: 35 passed, 585 deselected.
- AgentKit build_context regression tests passed: 7 passed, 30 deselected.

Remaining risks:

- G18 routes KB grounding through context packs but does not migrate MemoryPlugin injection; evidence-backed memory records remain G22/G23.
- G18 keeps KBPlugin on synchronous generic KB search for existing hook compatibility; richer source-specific retrieval composition remains future pack-building work.
