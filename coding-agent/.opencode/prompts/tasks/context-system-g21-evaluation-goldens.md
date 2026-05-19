Goal:
Add deterministic evaluation golden cases for retrieval and context-pack behavior.

Scope:
- Add local YAML golden cases that seed repo files and test-failure fixtures.
- Add an app-level evaluator that indexes the local fixtures, runs the existing `KBPlugin.build_context` hook, and validates rendered context-pack expectations.
- Use fake embeddings and local files only.
- Keep AgentKit Core untouched.

Out of scope:
- DeepEval, real LLM calls, credentials, external services, or metric judging.
- Memory evidence persistence or injection changes.
- Rewriting KBPlugin or AgentKit pipeline behavior.

Context:
- ADR:
  - `docs/adr/0034-context-system-boundaries-and-evidence.md`
- Existing implementation:
  - `src/coding_agent/kb.py`
  - `src/coding_agent/plugins/kb.py`
  - `src/coding_agent/context_pack.py`
  - `src/coding_agent/evaluation/manifest.py`

Target tests:
- `uv run pytest tests/coding_agent/evaluation/test_context_system_goldens.py -v`
- `uv run pytest tests/coding_agent/evaluation/ -v`
- `uv run pytest tests/coding_agent/plugins/test_kb.py tests/coding_agent/plugins/test_kb_plugin.py tests/coding_agent/test_context_pack.py -v`
- `uv run pytest tests/coding_agent/ -k "context_pack or retrieval or memory or evaluation" -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context" -v`

Loop policy:
- Engineer writes failing golden-case tests first.
- Engineer implements the smallest app-layer evaluator that exercises existing product paths.
- Reviewer reports only P1/P2 issues.
- Engineer fixes accepted P1/P2 findings and reruns the target tests.

Stop conditions:
- Escalate if the change needs AgentKit runtime or pipeline changes.
- Escalate if local fixtures and fake embeddings cannot produce deterministic verification.
- Stop if external services or credentials become necessary.
- Stop after two failed fix iterations for the same failure.
