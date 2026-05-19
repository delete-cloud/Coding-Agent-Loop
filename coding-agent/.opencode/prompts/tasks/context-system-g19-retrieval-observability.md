Goal:
Add safe retrieval observability counters and spans for context-system retrieval.

Scope:
- Record Coding Agent retrieval and context-pack render spans from KBPlugin.
- Keep attributes metadata-only: counts, booleans, and bounded enum values.
- Prove trace attributes do not include raw prompt, message, content, result, secret, or text values.

Out of scope:
- AgentKit runtime or pipeline changes.
- External observability services or credentials.
- Memory evidence migration.
- Evaluation manifests and golden cases.

Context:
- ADRs:
  - docs/adr/0034-context-system-boundaries-and-evidence.md
- Relevant files:
  - src/coding_agent/plugins/kb.py
  - tests/coding_agent/plugins/test_kb_plugin.py
  - docs/context_system/GOAL_PROGRESS.md
- Postmortems:
  - postmortem/patterns/PM-0009-preserve-neutral-bare-anchor-semantics.md
  - postmortem/patterns/PM-0017-preserve-shared-bootstrap-contracts.md

Target tests:
- `uv run pytest tests/coding_agent/plugins/test_kb_plugin.py -k "retrieval_observability" -v`
- `uv run pytest tests/coding_agent/plugins/test_kb.py tests/coding_agent/plugins/test_kb_plugin.py -v`
- `uv run pytest tests/coding_agent/test_observability.py -v`
- `uv run pytest tests/coding_agent/ -k "context_pack or retrieval or memory or evaluation or observability" -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`

Loop policy:
- Engineer implements the smallest correct change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate if the change needs AgentKit runtime or pipeline changes.
- Escalate if safe observability requires raw prompt/content/message/result/secret/text attributes.
- Stop if deterministic local verification cannot be produced.
