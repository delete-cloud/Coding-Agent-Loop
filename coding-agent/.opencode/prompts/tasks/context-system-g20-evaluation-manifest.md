Goal:
Add a deterministic manifest-driven evaluation harness baseline for context-system cases.

Scope:
- Add an app-level evaluation manifest loader/runner under `coding_agent.evaluation`.
- Manifest cases reference local JSONL tape fixtures and YAML golden specs.
- Build existing `EvaluationTestCase` objects from manifest entries without external judges.
- Preserve JSONL tape compatibility and existing adapter behavior.

Out of scope:
- New retrieval/context-pack golden cases; those are G21.
- DeepEval execution, real LLM calls, credentials, or external services.
- AgentKit runtime or pipeline changes.
- Memory evidence persistence or rendering changes.

Context:
- ADR:
  - `docs/adr/0034-context-system-boundaries-and-evidence.md`
- Existing evaluation entrypoints:
  - `src/coding_agent/evaluation/adapter.py`
  - `tests/coding_agent/evaluation/test_adapter.py`
- New intended files:
  - `src/coding_agent/evaluation/manifest.py`
  - `tests/coding_agent/evaluation/test_manifest.py`
  - `data/eval/golden/context-system-manifest.yaml`

Target tests:
- `uv run pytest tests/coding_agent/evaluation/test_manifest.py -v`
- `uv run pytest tests/coding_agent/evaluation/test_adapter.py tests/coding_agent/evaluation/test_manifest.py -v`
- `uv run pytest tests/coding_agent/ -k "context_pack or retrieval or memory or evaluation" -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context" -v`

Loop policy:
- Engineer writes the failing manifest tests first.
- Engineer implements the smallest app-layer manifest loader/runner.
- Reviewer reports only P1/P2 issues.
- Engineer fixes accepted P1/P2 findings and reruns the target tests.

Stop conditions:
- Escalate if the change needs AgentKit runtime or pipeline changes.
- Escalate if deterministic local fixtures are insufficient.
- Stop if external services or production credentials become necessary.
- Stop after two failed fix iterations for the same failure.
