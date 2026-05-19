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

Status: merged via PR #213.

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

Status: merged via PR #214.

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

Status: merged via PR #215.

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

Status: merged via PR #216.

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

Status: merged via PR #217.

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

Status: merged via PR #218.

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

Status: merged via PR #219.

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

## G19 - Safe Retrieval Observability Counters And Spans

Status: merged via PR #220.

### Before

Goal id: G19

Intended files:

- `src/coding_agent/plugins/kb.py`
- `tests/coding_agent/plugins/test_kb_plugin.py`
- `docs/context_system/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/context-system-g19-retrieval-observability.md`

Verification commands:

- `uv run pytest tests/coding_agent/plugins/test_kb_plugin.py -k "retrieval_observability" -v`
- `uv run pytest tests/coding_agent/plugins/test_kb.py tests/coding_agent/plugins/test_kb_plugin.py -v`
- `uv run pytest tests/coding_agent/test_observability.py -v`
- `uv run pytest tests/coding_agent/ -k "context_pack or retrieval or memory or evaluation or observability" -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`

Stop criteria:

- Change requires AgentKit runtime or pipeline changes.
- Change requires raw prompt/content/message/result/secret/text attributes.
- Deterministic verification cannot be produced.
- More than two fix iterations fail for the same reason.

Postmortem routing:

- `src/coding_agent/plugins/kb.py` matches PM-0009 and PM-0017. Release checks: run focused tests for affected plugin/bootstrap behavior and review affected control-flow shape before shipping.

### After

Changed files:

- `src/coding_agent/plugins/kb.py`
- `tests/coding_agent/plugins/test_kb_plugin.py`
- `docs/context_system/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/context-system-g19-retrieval-observability.md`

Verification results:

- Red: `uv run pytest tests/coding_agent/plugins/test_kb_plugin.py -k "retrieval_observability" -v` failed with 2 failed, 9 deselected before retrieval spans were implemented.
- Green: `uv run pytest tests/coding_agent/plugins/test_kb_plugin.py -k "retrieval_observability" -v` passed with 2 passed, 9 deselected.
- `uv run ruff format src/coding_agent/plugins/kb.py tests/coding_agent/plugins/test_kb_plugin.py` left both files formatted.
- `uv run ruff format --check src/coding_agent/plugins/kb.py tests/coding_agent/plugins/test_kb_plugin.py` passed with both files already formatted.
- `git diff --check -- .` passed.
- `uv run pytest tests/coding_agent/plugins/test_kb.py tests/coding_agent/plugins/test_kb_plugin.py -v` passed with 18 passed.
- `uv run pytest tests/coding_agent/test_observability.py -v` passed with 7 passed.
- `uv run pytest tests/coding_agent/ -k "context_pack or retrieval or memory or evaluation or observability" -v` passed with 45 passed, 577 deselected.
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v` passed with 8 passed, 29 deselected.
- Local subagent review was attempted three times but did not return before timeout; per workflow override, manual P1/P2 review gate checked staged span attributes for query/content/path/source-id leakage and cache-hit/miss correctness.

Implementation notes:

- `KBPlugin` emits safe `retrieval.kb.search` spans for search misses and cache hits through the mounted `observation_sink`.
- `KBPlugin` emits a `context_pack.render` span around the existing context-pack renderer path.
- Span attributes are restricted to counts, booleans, and bounded enum-like labels; tests guard against prompt, message, result, text, secret, chunk content, and fixture path/source labels leaking through attributes.

Remaining risks:

- G19 observes the existing synchronous KB search path only; richer multi-source retrieval composition remains later context-system work.
- G19 does not add new exporter behavior beyond existing observation sink contracts.

## G20 - Manifest-Driven Deterministic Evaluation Harness Baseline

Status: merged via PR #221.

### Before

Goal id: G20

Intended files:

- `src/coding_agent/evaluation/manifest.py`
- `src/coding_agent/evaluation/__init__.py`
- `tests/coding_agent/evaluation/test_manifest.py`
- `data/eval/golden/context-system-manifest.yaml`
- `docs/context_system/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/context-system-g20-evaluation-manifest.md`

Verification commands:

- `uv run pytest tests/coding_agent/evaluation/test_manifest.py -v`
- `uv run pytest tests/coding_agent/evaluation/test_adapter.py tests/coding_agent/evaluation/test_manifest.py -v`
- `uv run pytest tests/coding_agent/ -k "context_pack or retrieval or memory or evaluation" -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context" -v`

Stop criteria:

- Change requires AgentKit runtime or pipeline changes.
- Change requires external LLM calls, DeepEval, production credentials, or external services.
- Change breaks JSONL tape fixture compatibility.
- Deterministic verification cannot be produced.
- More than two fix iterations fail for the same reason.

Postmortem routing:

- Intended G20 files do not match any `postmortem/index.yaml` `related_files` entry.

### After

Changed files:

- `src/coding_agent/evaluation/manifest.py`
- `src/coding_agent/evaluation/__init__.py`
- `tests/coding_agent/evaluation/test_manifest.py`
- `data/eval/golden/context-system-manifest.yaml`
- `docs/context_system/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/context-system-g20-evaluation-manifest.md`

Verification results:

- Red: `uv run pytest tests/coding_agent/evaluation/test_manifest.py -v` failed during collection because `EvaluationManifest` was not exported yet.
- Green: `uv run pytest tests/coding_agent/evaluation/test_manifest.py -v` passed with 3 passed.
- Manual review regression: `uv run pytest tests/coding_agent/evaluation/test_manifest.py -v` failed when a list-key YAML fixture errored before schema validation; the regression was adjusted to use a numeric metadata key and passed with 4 passed.
- `uv run pytest tests/coding_agent/evaluation/test_adapter.py tests/coding_agent/evaluation/test_manifest.py -v` passed with 10 passed.
- `uv run pytest tests/coding_agent/evaluation/ -v` passed with 14 passed.
- `uv run ruff format --check src/coding_agent/evaluation tests/coding_agent/evaluation` passed with 8 files already formatted.
- `uv run ruff check src/coding_agent/evaluation tests/coding_agent/evaluation` passed.
- `git diff --check -- .` passed.
- `uv run pytest tests/coding_agent/ -k "context_pack or retrieval or memory or evaluation" -v` passed with 41 passed, 585 deselected.
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context" -v` passed with 7 passed, 30 deselected.
- Local subagent review was attempted but did not return before timeout; per workflow override, manual P1/P2 review checked path resolution, JSONL/golden compatibility, external-service boundaries, and manifest schema validation.

Implementation notes:

- Added a YAML manifest loader for local fixture-backed evaluation cases.
- Added `build_manifest_test_cases` to construct existing `EvaluationTestCase` objects from manifest entries without DeepEval, external LLM calls, or credentials.
- Manifest-derived cases preserve JSONL tape/golden-spec adapter behavior and add manifest metadata under explicit metadata keys.

Remaining risks:

- G20 uses the existing parent/child golden fixture to prove manifest mechanics; retrieval and context-pack-specific golden cases remain G21.
- The manifest runner builds local deterministic cases only; metric execution and external judges remain optional adapter behavior outside this baseline.

## G21 - Evaluation Golden Cases For Retrieval And Context-Pack Behavior

Status: merged via PR #222.

### Before

Goal id: G21

Intended files:

- `src/coding_agent/evaluation/context_system.py`
- `src/coding_agent/evaluation/__init__.py`
- `tests/coding_agent/evaluation/test_context_system_goldens.py`
- `data/eval/golden/context-system-retrieval-context-pack.yaml`
- `docs/context_system/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/context-system-g21-evaluation-goldens.md`

Verification commands:

- `uv run pytest tests/coding_agent/evaluation/test_context_system_goldens.py -v`
- `uv run pytest tests/coding_agent/evaluation/ -v`
- `uv run pytest tests/coding_agent/plugins/test_kb.py tests/coding_agent/plugins/test_kb_plugin.py tests/coding_agent/test_context_pack.py -v`
- `uv run pytest tests/coding_agent/ -k "context_pack or retrieval or memory or evaluation" -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context" -v`

Stop criteria:

- Change requires AgentKit runtime or pipeline changes.
- Change requires external LLM calls, DeepEval, production credentials, or external services.
- Golden cases cannot run deterministically with local fixtures and fake embeddings.
- Change duplicates production context-pack injection instead of exercising the existing `KBPlugin.build_context` path.
- More than two fix iterations fail for the same reason.

Postmortem routing:

- Intended G21 files do not match any `postmortem/index.yaml` `related_files` entry.

### After

Changed files:

- `src/coding_agent/evaluation/context_system.py`
- `src/coding_agent/evaluation/__init__.py`
- `tests/coding_agent/evaluation/test_context_system_goldens.py`
- `data/eval/golden/context-system-retrieval-context-pack.yaml`
- `docs/context_system/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/context-system-g21-evaluation-goldens.md`

Verification results:

- Red: `uv run pytest tests/coding_agent/evaluation/test_context_system_goldens.py -v` failed during collection because the context-system golden evaluator was not exported yet.
- Green: `uv run pytest tests/coding_agent/evaluation/test_context_system_goldens.py -v` passed with 3 passed.
- Local review fix red: `uv run pytest tests/coding_agent/evaluation/test_context_system_goldens.py -k "reuse_workspace" -v` failed when stale KB rows in a reused workspace displaced the test-failure section on the second run.
- Local review fix green: `uv run pytest tests/coding_agent/evaluation/test_context_system_goldens.py -k "unsafe_case_id or fixture_escape or reuse_workspace" -v` passed with 3 passed, 3 deselected after adding fresh per-run case directories, safe case-id validation, and fixture-root bounding.
- Final focused: `uv run pytest tests/coding_agent/evaluation/test_context_system_goldens.py -v` passed with 6 passed.
- `uv run ruff format --check src/coding_agent/evaluation tests/coding_agent/evaluation` initially failed because the new evaluator needed formatting; after `uv run ruff format src/coding_agent/evaluation/context_system.py tests/coding_agent/evaluation/test_context_system_goldens.py`, it passed with 10 files already formatted.
- `uv run ruff check src/coding_agent/evaluation tests/coding_agent/evaluation` initially failed on one unused import; after removing it, the command passed.
- `git diff --check -- .` passed.
- `uv run pytest tests/coding_agent/evaluation/ -v` passed with 20 passed.
- `uv run pytest tests/coding_agent/plugins/test_kb.py tests/coding_agent/plugins/test_kb_plugin.py tests/coding_agent/test_context_pack.py -v` passed with 22 passed.
- `uv run pytest tests/coding_agent/ -k "context_pack or retrieval or memory or evaluation" -v` passed with 47 passed, 585 deselected.
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context" -v` passed with 7 passed, 30 deselected.
- Local subagent review reported two P2 findings: reused workspaces could accumulate stale KB rows, and case/fixture paths needed stronger bounds. Both were fixed and covered by regression tests.

Implementation notes:

- Added a context-system golden evaluator that creates a local repo fixture, indexes repo and test-failure evidence into KB, and exercises the existing `KBPlugin.build_context` path.
- Added a golden case that proves auth retrieval renders both repo references and test-failure context pack sections while excluding an unrelated billing fixture.
- Added a negative golden test proving missing expected rendered snippets fail deterministically.
- Golden evaluation now uses fresh temporary case directories under the caller-provided workspace and validates fixture paths under `tests/fixtures`.

Remaining risks:

- G21 covers KB-backed retrieval/context-pack behavior only; memory evidence golden behavior remains G22/G23.
- G21 uses a purpose-built fake embedder for deterministic fixture ranking and does not attempt to evaluate production embedding quality.

## G22 - Memory Records With Evidence References And JSONL-Compatible Migration

Status: merged via PR #223.

### Before

Goal id: G22

Intended files:

- `src/coding_agent/plugins/memory.py`
- `tests/coding_agent/plugins/test_memory.py`
- `docs/context_system/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/context-system-g22-memory-evidence.md`

Verification commands:

- `uv run pytest tests/coding_agent/plugins/test_memory.py -k "evidence or Persistence" -v`
- `uv run pytest tests/coding_agent/plugins/test_memory.py -v`
- `uv run pytest tests/coding_agent/ -k "context_pack or retrieval or memory or evaluation" -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context" -v`

Stop criteria:

- Change requires AgentKit runtime or pipeline changes.
- Change requires changing `agentkit.directive.types.MemoryRecord`.
- Change requires changing memory rendering/injection semantics; that belongs to G23.
- Change breaks loading legacy JSONL `memory_record` payloads without evidence.
- Deterministic verification cannot be produced.
- More than two fix iterations fail for the same reason.

Postmortem routing:

- `src/coding_agent/plugins/memory.py` and `tests/coding_agent/plugins/test_memory.py` match PM-0009. Release checks: run focused affected memory tests and review control-flow shape before shipping.

### After

Changed files:

- `src/coding_agent/plugins/memory.py`
- `tests/coding_agent/plugins/test_memory.py`
- `docs/context_system/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/context-system-g22-memory-evidence.md`

Verification results:

- Red: `uv run pytest tests/coding_agent/plugins/test_memory.py -k "evidence or Persistence" -v` failed with 3 failed, 17 deselected before memory records carried evidence.
- Local review found one P2 issue: malformed persisted evidence with `line_end < line_start` preserved an invalid range. Red: `uv run pytest tests/coding_agent/plugins/test_memory.py -k "invalid_persisted_evidence_ranges" -v` failed with 1 failed, 20 deselected before range normalization.
- Green: `uv run pytest tests/coding_agent/plugins/test_memory.py -k "invalid_persisted_evidence_ranges" -v` passed with 1 passed, 20 deselected after invalid ranges were stripped.
- Green: `uv run pytest tests/coding_agent/plugins/test_memory.py -k "evidence or Persistence" -v` passed with 4 passed, 17 deselected.
- `uv run ruff format --check src/coding_agent/plugins/memory.py tests/coding_agent/plugins/test_memory.py` passed with both files already formatted after formatting the touched test file.
- `uv run ruff check src/coding_agent/plugins/memory.py tests/coding_agent/plugins/test_memory.py` passed after removing unused imports from the touched test file.
- `git diff --check -- .` passed.
- `uv run pytest tests/coding_agent/plugins/test_memory.py -v` passed with 21 passed.
- `uv run pytest tests/coding_agent/ -k "context_pack or retrieval or memory or evaluation" -v` passed with 49 passed, 585 deselected.
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context" -v` passed with 7 passed, 30 deselected.

Implementation notes:

- New working memories infer JSON-safe repo-file evidence references from file-like tags.
- Topic compaction merges repo-file evidence from topic files and working memories before persisting JSONL memory records.
- Legacy JSONL memory records without `evidence` still load, receive importance decay, and normalize to `evidence: []`.
- Malformed persisted evidence ranges are normalized by dropping invalid line fields before future context-pack rendering consumes them.
- Memory rendering/injection behavior is intentionally unchanged for G22.

Remaining risks:

- G22 records evidence but still renders legacy `[Memory]` grounding; G23 owns rendering memory as context-pack reference material and omitting unevidenced memories.
- Evidence inference is intentionally conservative and file-tag based; richer tape-entry/session/test-failure evidence can be added without breaking the JSONL shape.

## G23 - Memory Retrieval And Injection As Evidence-Backed Reference Context

Status: merged via PR #224.

### Before

Goal id: G23

Intended files:

- `src/coding_agent/plugins/memory.py`
- `src/coding_agent/context_pack.py`
- `tests/coding_agent/plugins/test_memory.py`
- `tests/coding_agent/test_context_pack.py`
- `docs/context_system/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/context-system-g23-memory-context-pack.md`

Verification commands:

- `uv run pytest tests/coding_agent/plugins/test_memory.py -k "build_context or TopicScopedRecall" -v`
- `uv run pytest tests/coding_agent/test_context_pack.py -k "memory" -v`
- `uv run pytest tests/coding_agent/plugins/test_memory.py tests/coding_agent/test_context_pack.py -v`
- `uv run pytest tests/coding_agent/ -k "context_pack or retrieval or memory or evaluation" -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context" -v`

Stop criteria:

- Change requires AgentKit runtime, `ContextBuilder`, or directive schema changes.
- Change renders memory as instructions or policy rather than reference material.
- Change injects unevidenced memory by default.
- Change breaks topic-file filtering or importance ordering for memory recall.
- Deterministic verification cannot be produced.
- More than two fix iterations fail for the same reason.

Postmortem routing:

- `src/coding_agent/plugins/memory.py`, `src/coding_agent/context_pack.py`, `tests/coding_agent/plugins/test_memory.py`, and `tests/coding_agent/test_context_pack.py` match PM-0009. Release checks: run focused affected tests and review control-flow shape before shipping.

### After

Changed files:

- `src/coding_agent/plugins/memory.py`
- `src/coding_agent/context_pack.py`
- `tests/coding_agent/plugins/test_memory.py`
- `tests/coding_agent/test_context_pack.py`
- `docs/context_system/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/context-system-g23-memory-context-pack.md`

Verification results:

- Baseline: `uv run pytest tests/coding_agent/plugins/test_memory.py -v` passed with 21 passed before G23 edits.
- Red: `uv run pytest tests/coding_agent/plugins/test_memory.py -k "build_context or TopicScopedRecall" -v` failed with 3 failed, 3 passed, 16 deselected because memory still used legacy `[Memory]` rendering and unevidenced injection.
- Red: `uv run pytest tests/coding_agent/test_context_pack.py -k "memory_session_evidence" -v` failed with 1 failed, 4 deselected because `EvidenceRef` did not accept session/tape-entry evidence fields.
- Green: `uv run pytest tests/coding_agent/plugins/test_memory.py -k "build_context or TopicScopedRecall" -v` passed with 6 passed, 16 deselected.
- Green: `uv run pytest tests/coding_agent/test_context_pack.py -k "memory" -v` passed with 3 passed, 2 deselected.
- `uv run ruff format --check src/coding_agent/plugins/memory.py src/coding_agent/context_pack.py tests/coding_agent/plugins/test_memory.py tests/coding_agent/test_context_pack.py` passed with all four files already formatted.
- `uv run ruff check src/coding_agent/plugins/memory.py src/coding_agent/context_pack.py tests/coding_agent/plugins/test_memory.py tests/coding_agent/test_context_pack.py` passed.
- `git diff --check -- .` passed.
- `uv run pytest tests/coding_agent/plugins/test_memory.py tests/coding_agent/test_context_pack.py -v` passed with 27 passed.
- `uv run pytest tests/coding_agent/ -k "context_pack or retrieval or memory or evaluation" -v` passed with 51 passed, 585 deselected.
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context" -v` passed with 7 passed, 30 deselected.

Implementation notes:

- MemoryPlugin now renders recalled memories through `ContextPackRenderer` as a single `Memory references` section.
- Memory records without evidence are omitted from grounding by default.
- Topic-file filtering and importance ordering still choose memory candidates before rendering.
- Memory context-pack source ids are deterministic hashes of normalized summary, tags, and evidence.
- ContextPack `EvidenceRef` now supports optional `session_id` and `tape_entry_id` fields for memory evidence.

Remaining risks:

- MemoryPlugin still uses simple tag overlap for memory retrieval; richer memory ranking is outside G23.
- Unevidenced legacy memories remain persisted and loadable but are intentionally invisible to default grounding.

## G24 - End-To-End Context-System Smoke, Implementation Report, And Final Audit

Status: passed local verification; pending PR.

### Before

Goal id: G24

Intended files:

- `tests/coding_agent/test_context_system_smoke.py`
- `docs/context_system/IMPLEMENTATION_REPORT.md`
- `docs/context_system/GOAL_PROGRESS.md`
- `docs/adr/0034-context-system-boundaries-and-evidence.md`
- `.opencode/prompts/tasks/context-system-g24-final-audit.md`

Verification commands:

- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/test_kb.py -k "repo_chunk_metadata_records_source_kind_and_repo_path or repo_retrieval_returns_ranked_evidence_with_fake_embedder or failure_retrieval_indexes_pytest_failure_evidence" -v`
- `uv run pytest tests/coding_agent/evaluation/ -v`
- `uv run pytest tests/coding_agent/plugins/test_kb.py tests/coding_agent/plugins/test_kb_plugin.py tests/coding_agent/plugins/test_memory.py tests/coding_agent/test_context_pack.py tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/coding_agent/ -k "context_pack or retrieval or memory or evaluation" -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`

Stop criteria:

- End-to-end smoke requires AgentKit runtime, `ContextBuilder`, or directive schema changes.
- Verification requires external services, external judges, production credentials, or remote vector stores.
- Final audit finds an unmet ADR-0034 acceptance criterion without executable test coverage.
- Deterministic verification cannot be produced.
- More than two fix iterations fail for the same reason.

Postmortem routing:

- `tests/coding_agent/test_context_system_smoke.py`, `docs/context_system/GOAL_PROGRESS.md`, and `docs/adr/0034-context-system-boundaries-and-evidence.md` match PM-0009. Release checks: run focused affected tests and review control-flow shape before shipping.

### After

Changed files:

- `tests/coding_agent/test_context_system_smoke.py`
- `docs/context_system/IMPLEMENTATION_REPORT.md`
- `docs/context_system/GOAL_PROGRESS.md`
- `docs/adr/0034-context-system-boundaries-and-evidence.md`
- `.opencode/prompts/tasks/context-system-g24-final-audit.md`

Verification results:

- `uv run ruff format --check tests/coding_agent/test_context_system_smoke.py` passed with the file already formatted after running `uv run ruff format tests/coding_agent/test_context_system_smoke.py`.
- `uv run ruff check tests/coding_agent/test_context_system_smoke.py` passed.
- `git diff --check -- .` passed.
- Green: `uv run pytest tests/coding_agent/test_context_system_smoke.py -v` passed with 1 passed.
- `uv run pytest tests/test_kb.py -k "repo_chunk_metadata_records_source_kind_and_repo_path or repo_retrieval_returns_ranked_evidence_with_fake_embedder or failure_retrieval_indexes_pytest_failure_evidence" -v` passed with 3 passed, 41 deselected.
- `uv run pytest tests/coding_agent/evaluation/ -v` passed with 20 passed.
- `uv run pytest tests/coding_agent/plugins/test_kb.py tests/coding_agent/plugins/test_kb_plugin.py tests/coding_agent/plugins/test_memory.py tests/coding_agent/test_context_pack.py tests/coding_agent/test_context_system_smoke.py -v` passed with 46 passed.
- `uv run pytest tests/coding_agent/ -k "context_pack or retrieval or memory or evaluation" -v` passed with 52 passed, 585 deselected.
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v` passed with 8 passed, 29 deselected.

Implementation notes:

- Added a deterministic smoke test that builds a Pipeline with KBPlugin and MemoryPlugin, indexes repo and test-failure evidence, and verifies the composed `build_context` prompt contains repo, failure, and memory reference context.
- Updated the final implementation report with landed goal and ADR acceptance evidence.
- Marked ADR-0034 accepted and checked off the executable acceptance criteria.
- Updated the phase ledger with merged PR status for G12-G23.

Remaining risks:

- G24 is a final smoke/audit goal; it does not add new ranking, indexing, or memory capture policy beyond prior goals.
