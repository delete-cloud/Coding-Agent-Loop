# ADR-0009: E2E tests can opt into real providers and storage-backed persistence

**Status**: Proposed
**Date**: 2026-04-14

## Context

The repository already has strong layered coverage for runtime execution, tape extraction, and evaluation adaptation. Integration tests under `tests/integration/` execute real pipeline turns with mocked providers, while evaluation tests consume curated JSONL fixtures.

That leaves two gaps for higher-confidence end-to-end verification. First, integration tests cannot currently exercise a real provider call path without editing the tests, because they always replace the provider with an `AsyncMock`. Second, the new persisted-tape evaluation flow is currently validated with direct `Tape.save_jsonl(...)`, not through the storage plugin's `ForkTapeStore`/`JSONLTapeStore` commit/load path.

We want a stronger test path without making the default test suite flaky, slow, or credential-dependent. The framework needs an explicit way to run real-provider E2E checks only when the environment is prepared, while keeping the default suite deterministic.

## Decision

Add an opt-in E2E test path that can run against a real provider when the required environment variables are present, and otherwise skips cleanly. Keep the default integration tests mock-driven.

Also add a storage-backed E2E test that verifies persisted tape data through `ForkTapeStore` + `JSONLTapeStore`, instead of only through direct `Tape.save_jsonl(...)`.

Metric-level evaluation is a separate opt-in layer on top of this decision. The base E2E boundary for this ADR is runtime execution -> persisted tape -> extraction -> evaluation test-case construction. A metric-level test may be added later, but it must remain optional when the `deepeval` dependency is not installed. If we add a longest-chain test that combines a real provider, subagent/tool execution, persistence, extraction, and metric evaluation, it must also remain opt-in and use assertions that tolerate provider-side variance while still proving the intended chain occurred.

## Alternatives Rejected

- Always run integration tests against real providers — rejected because it would make the default suite credential-dependent, slower, and flaky.
- Keep only mock-provider integration tests — rejected because it leaves no supported path to verify real provider wiring in CI or local smoke checks.
- Add always-on metric-level DeepEval integration in the same change — rejected because it expands scope beyond provider/storage boundary verification and would make the default suite depend on an optional external package boundary.
- Require brittle exact-output assertions from a real provider plus subagent longest-chain test — rejected because provider-side variance would make the test flaky; the correct contract is that the chain executes and produces evaluable tool traces, not that every natural-language token is identical.

## Acceptance Criteria

- [ ] `tests/integration/test_pipeline_e2e.py::TestPipelineE2E::test_subagent_turn_persisted_tape_flows_into_eval_adapter`
- [ ] `tests/integration/test_pipeline_e2e.py::TestPipelineE2E::test_storage_backed_persisted_tape_round_trip`
- [ ] `tests/integration/test_pipeline_e2e.py::TestPipelineE2E::test_real_provider_e2e_turn_skips_without_credentials`
- [ ] `tests/integration/test_pipeline_e2e.py::TestPipelineE2E::test_tool_correctness_metric_accepts_built_test_case`
- [ ] `tests/integration/test_pipeline_e2e.py::TestPipelineE2E::test_real_provider_subagent_metric_chain`
- [ ] `uv run pytest tests/integration/test_pipeline_e2e.py -k "persisted_tape_flows_into_eval_adapter or storage_backed_persisted_tape_round_trip or real_provider_e2e_turn" -v`

## References

- `src/coding_agent/app.py`
- `src/coding_agent/plugins/llm_provider.py`
- `src/coding_agent/plugins/storage.py`
- `src/agentkit/tape/store.py`
- `src/agentkit/tape/tape.py`
- `src/agentkit/tape/extract.py`
- `src/coding_agent/evaluation/adapter.py`
- `tests/integration/test_pipeline_e2e.py`
