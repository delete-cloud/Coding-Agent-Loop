Goal:
Record the current Context System + Evaluation baseline and create the phase ledger for G12-G24.

Scope:
- Document current KB, Memory, build_context, observability, and evaluation entrypoints.
- Define the sequential G12-G24 goal map because no existing repository document defines those goal ids.
- Create the task packet and progress ledger that later goals will update before and after implementation.

Out of scope:
- Production code changes.
- ADR creation for G12.
- Retrieval, memory, observability, or evaluation behavior changes.
- Durable runtime changes.

Context:
- ADRs:
  - `docs/adr/0007-task-packets-are-the-verification-contract.md`
  - `docs/adr/0008-structured-tape-extraction-belongs-to-agentkit.md`
  - `docs/adr/0028-observability-and-langfuse-integration.md`
- Relevant files:
  - `src/agentkit/runtime/hookspecs.py`
  - `src/agentkit/runtime/pipeline.py`
  - `src/agentkit/context/builder.py`
  - `src/coding_agent/kb.py`
  - `src/coding_agent/plugins/kb.py`
  - `src/coding_agent/plugins/memory.py`
  - `src/coding_agent/evaluation/adapter.py`
  - `src/coding_agent/observability.py`
  - `tests/coding_agent/plugins/test_kb.py`
  - `tests/coding_agent/plugins/test_kb_plugin.py`
  - `tests/coding_agent/plugins/test_memory.py`
  - `tests/coding_agent/evaluation/test_adapter.py`
  - `tests/agentkit/runtime/test_pipeline.py`

Target tests:
- `test -f docs/context_system/GOAL_PROGRESS.md`
- `test -f docs/context_system/CURRENT_STATE.md`
- `test -f .opencode/prompts/tasks/context-system-g12-current-state.md`
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
- Stop if current KB/RAG/Memory/build_context/evaluation entrypoints cannot be identified.
- Stop if a docs-only current-state audit requires production runtime changes.
