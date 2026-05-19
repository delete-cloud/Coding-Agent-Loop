Goal:
Render Coding Agent memory as evidence-backed context-pack reference material instead of legacy memory grounding.

Scope:
- Convert `MemoryPlugin.build_context` output to a rendered `ContextPack` memory section.
- Omit memories without evidence from grounding by default.
- Preserve topic-file filtering and importance ordering for memory selection.
- Support memory evidence refs that include session and tape-entry identifiers.

Out of scope:
- AgentKit pipeline, `ContextBuilder`, or directive schema changes.
- Changing how new memory records are produced or persisted beyond rendering needs.
- External services, vector stores, or production credentials.

Context:
- ADR:
  - `docs/adr/0034-context-system-boundaries-and-evidence.md`
- Relevant files:
  - `src/coding_agent/plugins/memory.py`
  - `src/coding_agent/context_pack.py`
  - `tests/coding_agent/plugins/test_memory.py`
  - `tests/coding_agent/test_context_pack.py`
- Postmortem:
  - `postmortem/patterns/PM-0009-preserve-neutral-bare-anchor-semantics.md`

Target tests:
- `uv run pytest tests/coding_agent/plugins/test_memory.py -k "build_context or TopicScopedRecall" -v`
- `uv run pytest tests/coding_agent/test_context_pack.py -k "memory" -v`
- `uv run pytest tests/coding_agent/plugins/test_memory.py tests/coding_agent/test_context_pack.py -v`
- `uv run pytest tests/coding_agent/ -k "context_pack or retrieval or memory or evaluation" -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context" -v`

Loop policy:
- Engineer writes failing memory-rendering tests first.
- Engineer implements the smallest coding-agent-only rendering change.
- Reviewer reports only P1/P2 issues.
- Engineer fixes accepted P1/P2 findings and reruns target tests.

Stop conditions:
- Stop if the change requires AgentKit runtime or `ContextBuilder` changes.
- Stop if memory records need to become system instructions to satisfy tests.
- Stop if unevidenced memories cannot be omitted without breaking deterministic behavior.
- Stop after two failed fix iterations for the same failure.
