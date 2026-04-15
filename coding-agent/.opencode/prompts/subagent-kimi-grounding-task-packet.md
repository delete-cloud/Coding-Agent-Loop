Goal:
Make child subagent execution less misleading by preserving child approval context, surfacing child identity in approval previews, and explicitly grounding child sessions that nested subagent delegation is unavailable.

Scope:
- Fix child approval request propagation so child agent IDs survive the adapter approval bridge.
- Improve the approval preview so child-originated requests visibly identify the child agent.
- Add explicit child-session system-prompt grounding that nested `subagent` delegation is unavailable in child runs.
- Make session-wide approval caching origin-aware so child approvals do not silently auto-approve parent requests for the same tool.
- Add or update only the focused regression tests needed for adapter, UI approval preview, and child prompt construction.

Out of scope:
- Do not redesign tape extraction or evaluation adapters.
- Do not modify PR 52 or PR 53 branches.
- Do not change persisted tape semantics, wire protocol structure, or HTTP event formats unless the focused fix proves that necessary.
- Do not broaden this into generic provider hallucination mitigation beyond the child-session grounding we control in this repo.

Context:
- ADRs:
  - `docs/adr/0008-structured-tape-extraction-belongs-to-agentkit.md`
  - `docs/adr/0009-e2e-tests-can-opt-into-real-providers-and-storage-backed-persistence.md`
- Relevant files:
  - `src/coding_agent/adapter.py`
  - `src/coding_agent/app.py`
  - `src/coding_agent/tools/subagent.py`
  - `src/coding_agent/ui/approval_prompt.py`
  - `src/coding_agent/ui/rich_consumer.py`
  - `tests/coding_agent/test_pipeline_adapter.py`
  - `tests/ui/test_approval_prompt.py`
  - `tests/ui/test_streaming_consumer.py`
  - `tests/coding_agent/tools/test_subagent.py`

Target tests:
- `uv run pytest tests/coding_agent/test_pipeline_adapter.py -k "approval or child_consumer_emits_agent_id" -v`
- `uv run pytest tests/ui/test_approval_prompt.py -v`
- `uv run pytest tests/ui/test_streaming_consumer.py -k "session_approve or child_origin" -v`
- `uv run pytest tests/coding_agent/tools/test_subagent.py -k "nested_subagent or prompt" -v`

Loop policy:
- Engineer implements the smallest correct change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- If the fix requires changing persisted tape semantics, wire protocol shape, or cross-module ownership, stop and write an ADR first.
- Ignore non-blocking optimization suggestions.
