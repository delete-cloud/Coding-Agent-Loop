# Context System + Evaluation Implementation Report

Date: 2026-05-19
Scope: G12-G24

## Summary

The Context System + Evaluation phase is implemented in Coding Agent without moving repository, failure, memory, or evaluation semantics into AgentKit Core. AgentKit still composes plugin-supplied context through the existing `build_context` hook; Coding Agent owns retrieval, context packs, evidence policy, observability counters, and deterministic evaluation fixtures.

Memory grounding now follows the ADR authority boundary: evidence-backed memories render as reference context and unevidenced legacy memories are omitted from default grounding. Repo chunks and test failures render through the same context-pack model as memory references.

## Landed Goals

| Goal | Result |
| --- | --- |
| G12 | Current-state audit and phase ledger landed in PR #213. |
| G13 | ADR-0034 context-system boundary decision landed in PR #214. |
| G14 | Repo-aware chunk metadata landed in PR #215. |
| G15 | Deterministic repo retrieval fixtures landed in PR #216. |
| G16 | Test-failure ingest/search fixtures landed in PR #217. |
| G17 | Context-pack model and renderer landed in PR #218. |
| G18 | Context-pack injection through `build_context` landed in PR #219. |
| G19 | Safe retrieval observability counters landed in PR #220. |
| G20 | Manifest-driven evaluation baseline landed in PR #221. |
| G21 | Retrieval/context-pack golden cases landed in PR #222. |
| G22 | Memory evidence records and JSONL-compatible migration landed in PR #223. |
| G23 | Memory context-pack rendering and unevidenced-memory omission landed in PR #224. |
| G24 | End-to-end smoke, final audit, and this report are covered by `tests/coding_agent/test_context_system_smoke.py`. |

## Acceptance Audit

| ADR-0034 criterion | Executable evidence |
| --- | --- |
| Repo chunk metadata records source kind and repo path | `tests/test_kb.py::TestKBIndexing::test_repo_chunk_metadata_records_source_kind_and_repo_path` |
| Repo retrieval returns ranked evidence with fake embedder | `tests/test_kb.py::TestKBSearch::test_repo_retrieval_returns_ranked_evidence_with_fake_embedder` |
| Failure retrieval indexes pytest failure evidence | `tests/test_kb.py::TestKBSearch::test_failure_retrieval_indexes_pytest_failure_evidence` |
| Context-pack renderer labels memory as reference | `tests/coding_agent/test_context_pack.py::test_context_pack_renderer_labels_memory_as_reference` |
| Context-pack injection uses `build_context` without pipeline rewrite | `tests/coding_agent/plugins/test_kb_plugin.py::test_context_pack_injection_uses_build_context_without_pipeline_rewrite` |
| Retrieval observability emits safe counters only | `tests/coding_agent/plugins/test_kb_plugin.py::test_retrieval_observability_emits_counts_without_sensitive_attributes` |
| Evaluation manifest builds context-system cases from fixtures | `tests/coding_agent/evaluation/test_manifest.py::test_evaluation_manifest_builds_context_system_cases_from_local_fixtures` |
| Memory records persist evidence and load legacy records | `tests/coding_agent/plugins/test_memory.py::TestMemoryPersistence::test_mount_loads_persisted_memory_records_with_importance_decay` |
| Memory without evidence is not rendered as instruction | `tests/coding_agent/plugins/test_memory.py::TestMemoryPlugin::test_build_context_omits_unevidenced_memory_by_default` |
| End-to-end build-context composition covers repo, failure, and memory evidence | `tests/coding_agent/test_context_system_smoke.py::test_context_system_smoke_combines_retrieval_failure_and_memory` |

## Durable Baseline

The G24 smoke test proves the integrated path that matters for runtime safety: `Pipeline._stage_build_context` gathers Coding Agent KB and memory plugin context through HookRuntime, composes it through AgentKit `ContextBuilder`, and produces LLM-visible reference grounding without a pipeline rewrite.

The durable baseline remains local and deterministic:

- Fake embedders and local fixtures are used for retrieval and evaluation.
- No external LLM, external judge, production credentials, or remote vector store is required.
- Observability tests assert metadata-only retrieval attributes and avoid raw prompt/content/result text in span attributes.
- JSONL tape and memory compatibility are covered by evaluation adapter tests and memory persistence tests.
- AgentKit `build_context` and runtime-stage span tests remain the guardrail for generic runtime behavior.

## Residual Risks

- Retrieval quality is deterministic and fixture-proven, not production-tuned. Richer ranking/query shaping remains future work.
- Memory evidence inference is conservative and mostly file-tag based. Session, tape-entry, command, and failure evidence fields are supported, but richer capture policy can be expanded later.
- Unevidenced legacy memories remain persisted and loadable, but are intentionally hidden from default grounding.
- Evaluation fixtures cover representative local cases; broader benchmark coverage can be added without changing the AgentKit boundary.
