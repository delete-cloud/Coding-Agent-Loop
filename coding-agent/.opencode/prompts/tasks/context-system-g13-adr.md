Goal:
Write the ADR that bounds Context System + Evaluation implementation decisions for G14-G24.

Scope:
- Add ADR-0034 for context-system retrieval, context packs, memory evidence, retrieval observability, and evaluation boundaries.
- Update `docs/context_system/GOAL_PROGRESS.md` with G13 before/after evidence.
- Keep the change docs-only.

Out of scope:
- Production code changes.
- Test fixture implementation.
- Retrieval, memory, observability, or evaluation behavior changes.
- Durable runtime changes.

Context:
- ADRs:
  - `docs/adr/0007-task-packets-are-the-verification-contract.md`
  - `docs/adr/0008-structured-tape-extraction-belongs-to-agentkit.md`
  - `docs/adr/0028-observability-and-langfuse-integration.md`
  - `docs/adr/0034-context-system-boundaries-and-evidence.md`
- Relevant files:
  - `docs/context_system/CURRENT_STATE.md`
  - `docs/context_system/GOAL_PROGRESS.md`
  - `src/agentkit/runtime/hookspecs.py`
  - `src/agentkit/runtime/pipeline.py`
  - `src/agentkit/context/builder.py`
  - `src/coding_agent/kb.py`
  - `src/coding_agent/plugins/kb.py`
  - `src/coding_agent/plugins/memory.py`
  - `src/coding_agent/evaluation/adapter.py`
  - `src/coding_agent/observability.py`

Target tests:
- `test -f docs/adr/0034-context-system-boundaries-and-evidence.md`
- `test -f .opencode/prompts/tasks/context-system-g13-adr.md`
- `rg -n "## Context|## Decision|## Alternatives Rejected|## Acceptance Criteria|## References" docs/adr/0034-context-system-boundaries-and-evidence.md`
- `uv run pytest tests/coding_agent/plugins/test_kb.py tests/coding_agent/plugins/test_kb_plugin.py tests/coding_agent/plugins/test_memory.py tests/coding_agent/evaluation/test_adapter.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`

Loop policy:
- Engineer implements the smallest correct documentation change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate architectural redirection or scope expansion to the human.
- Ignore non-blocking optimization suggestions.
- Stop if the ADR would require rewriting AgentKit pipeline behavior.
- Stop if the ADR would require durable runtime semantic changes.
