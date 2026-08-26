# ADR-0034: Bound context-system retrieval, packs, evidence, and evaluation

**Status**: Accepted
**Date**: 2026-05-19

## Context

The next phase adds repo-aware retrieval, test-failure retrieval, context packs, retrieval observability, an evaluation harness, and memory with evidence. The current code already has the generic AgentKit `build_context` hook, `ContextBuilder`, a `coding_agent` KB plugin, a `coding_agent` memory plugin, provider-neutral observability primitives, and a tape-to-evaluation adapter.

The main design risk is boundary drift. Repo-specific retrieval, pytest failure parsing, memory policy, and evaluation fixtures are Coding Agent product behavior. AgentKit should remain a generic runtime that composes context supplied by plugins. This phase must not rewrite the pipeline or make AgentKit aware of repository semantics.

The second design risk is authority drift. Current memory is rendered as system-role grounding. Future memory must be treated as reference context with evidence, not as instructions or policy. Retrieval observability must also avoid exporting raw prompts, chunk content, failure output, memory summaries, tool results, secrets, or other sensitive text.

## Decision

Keep the AgentKit context boundary narrow:

- AgentKit continues to own `build_context`, context windowing, and message composition.
- Coding Agent owns repo-aware retrieval, test-failure retrieval, memory evidence policy, context-pack selection, context-pack rendering, and context/evaluation fixtures.
- Future context-pack objects should live in `coding_agent` unless a source-agnostic abstraction becomes clearly useful outside this app.
- Plugins inject rendered context through the existing `build_context` hook. They must not require changes to `_stage_build_context` or `ContextBuilder` for this phase.

Represent context as evidence-backed packs before rendering:

- A context pack is an ordered, JSON-safe app-level structure with sections such as repo references, test failures, memory references, and runtime hints.
- Each pack item has a source kind, stable source id, short label, optional location metadata, score/rank metadata when relevant, and evidence references.
- Repo evidence can reference repo-relative paths, line ranges when known, content hashes, and chunk ids.
- Test-failure evidence can reference a command label, exit code, test node id, repo-relative path, and bounded failure snippet selected for LLM-visible grounding.
- Memory evidence can reference repo files, tape entry ids, session ids, command labels, or test-failure ids. New memory records add evidence fields in a JSONL-compatible way. Old memory records without evidence remain loadable but must not be rendered as authoritative instructions.

Render context packs as reference grounding, not policy:

- Memory entries are never rendered as imperative instructions.
- Memory entries must be labelled as reference material and include evidence when injected.
- If a memory item has no evidence, it should be omitted from grounding by default or explicitly labelled as unevidenced reference if a later goal needs transitional compatibility.
- Pack rendering may still use system-role grounding messages because that is the existing `build_context` channel, but the rendered text must distinguish reference material from instructions.

Keep retrieval observability metadata-only:

- Retrieval and pack-building spans belong in `coding_agent`, using the existing provider-neutral observation sink.
- Safe attributes include counts, booleans, bounded enum values, durations, ranks, source kinds, and cache-hit state.
- Attribute keys must avoid sensitive substrings already filtered by the exporter, including `content`, `message`, `prompt`, `result`, `secret`, and `text`. Prefer keys such as `retrieval.candidate_count`, `retrieval.selected_count`, `retrieval.source_kind`, `retrieval.cache_hit`, `pack.section_count`, `pack.item_count`, and `memory.evidence_count`.
- Attribute values must not include raw user prompts, retrieved chunks, file contents, test output, memory summaries, tool arguments, tool results, environment values, or secrets.

Make evaluation deterministic and fixture-driven:

- Context-system evaluation lives in `coding_agent.evaluation` or adjacent app-level modules.
- Evaluation manifests should use local JSON/YAML fixtures and fake embedders/providers.
- DeepEval or external judges remain optional adapters, not required for local verification.
- JSONL tape compatibility remains part of the evaluation contract.

## Alternatives Rejected

- Put repo-aware retrieval and test-failure retrieval in AgentKit Core. Rejected because repo files, pytest failures, and Coding Agent memory policy are app-specific semantics.
- Rewrite the AgentKit pipeline around context packs. Rejected because the existing `build_context` hook and `ContextBuilder` already provide the required injection point.
- Render memory as high-priority system instructions. Rejected because memory can be stale or wrong and must be evidence-backed reference context.
- Require external vector stores, LLM judges, or production credentials for evaluation. Rejected because this phase requires deterministic local tests with fake providers and fixtures.
- Export raw queries, retrieved content, failure output, memory summaries, or tool results as trace attributes. Rejected because this violates the repository's observability safety boundary.

## Acceptance Criteria

Implementation of G14-G24 should add executable tests covering these contracts:

- [x] `test_repo_chunk_metadata_records_source_kind_and_repo_path`
- [x] `test_repo_retrieval_returns_ranked_evidence_with_fake_embedder`
- [x] `test_failure_retrieval_indexes_pytest_failure_evidence`
- [x] `test_context_pack_renderer_labels_memory_as_reference`
- [x] `test_context_pack_injection_uses_build_context_without_pipeline_rewrite`
- [x] `test_retrieval_observability_emits_counts_without_sensitive_attributes`
- [x] `test_evaluation_manifest_builds_context_system_cases_from_local_fixtures`
- [x] `test_memory_records_persist_evidence_and_load_legacy_records`
- [x] `test_memory_without_evidence_is_not_rendered_as_instruction`
- [x] `uv run pytest tests/coding_agent/ -k "context_pack or retrieval or memory or evaluation" -v`
- [x] `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context" -v`

## References

- `docs/context_system/CURRENT_STATE.md`
- `docs/context_system/GOAL_PROGRESS.md`
- `docs/adr/0007-task-packets-are-the-verification-contract.md`
- `docs/adr/0008-structured-tape-extraction-belongs-to-agentkit.md`
- `docs/adr/0028-observability-and-langfuse-integration.md`
- `src/agentkit/runtime/hookspecs.py`
- `src/agentkit/runtime/pipeline.py`
- `src/agentkit/context/builder.py`
- `src/coding_agent/kb.py`
- `src/coding_agent/plugins/kb.py`
- `src/coding_agent/plugins/memory.py`
- `src/coding_agent/evaluation/adapter.py`
- `src/coding_agent/observability.py`
