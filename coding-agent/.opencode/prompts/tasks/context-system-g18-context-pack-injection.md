Goal:
Inject rendered context packs through the existing Coding Agent `build_context` hook.

Scope:
- Update KBPlugin to render KB grounding with the G17 context pack renderer.
- Preserve the existing `mount` and `build_context` hook contract.
- Add focused tests proving context pack injection happens through plugin `build_context`.

Out of scope:
- AgentKit runtime or pipeline changes.
- Memory plugin evidence migration or memory context-pack injection.
- Retrieval observability spans or counters.
- Evaluation manifests and golden cases.

Context:
- ADRs:
  - docs/adr/0034-context-system-boundaries-and-evidence.md
- Relevant files:
  - src/coding_agent/plugins/kb.py
  - tests/coding_agent/plugins/test_kb.py
  - tests/coding_agent/plugins/test_kb_plugin.py
  - docs/context_system/GOAL_PROGRESS.md
- Postmortems:
  - postmortem/patterns/PM-0009-preserve-neutral-bare-anchor-semantics.md
  - postmortem/patterns/PM-0017-preserve-shared-bootstrap-contracts.md

Target tests:
- `uv run pytest tests/coding_agent/plugins/test_kb.py tests/coding_agent/plugins/test_kb_plugin.py -v`
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
- Escalate if the change needs AgentKit runtime or pipeline changes.
- Escalate if the change needs memory persistence format changes.
- Stop if deterministic local verification cannot be produced.
