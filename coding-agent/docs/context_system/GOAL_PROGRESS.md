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
