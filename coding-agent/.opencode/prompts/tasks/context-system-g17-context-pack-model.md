Goal:
Implement the Coding Agent context pack data model and renderer contract.

Scope:
- Add app-level context pack dataclasses for sections, items, and evidence references.
- Add a deterministic renderer that produces existing `build_context`-compatible messages.
- Ensure memory items render as reference material with evidence, not instructions.

Out of scope:
- Wiring context packs into KBPlugin or MemoryPlugin `build_context`.
- Retrieval observability spans or counters.
- Evaluation manifests and golden cases.
- AgentKit runtime or pipeline changes.

Context:
- ADRs:
  - docs/adr/0034-context-system-boundaries-and-evidence.md
- Relevant files:
  - src/coding_agent/context_pack.py
  - tests/coding_agent/test_context_pack.py
  - docs/context_system/GOAL_PROGRESS.md

Target tests:
- `uv run pytest tests/coding_agent/test_context_pack.py -v`
- `uv run pytest tests/coding_agent/ -k "context_pack or retrieval or memory or evaluation" -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context" -v`

Loop policy:
- Engineer implements the smallest correct change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate if the change needs `build_context` plugin wiring.
- Escalate if the change needs AgentKit runtime changes.
- Stop if deterministic local verification cannot be produced.
