Goal:
Add evidence references to Coding Agent persisted memory records while preserving legacy JSONL compatibility.

Scope:
- Add JSON-safe evidence references to new Coding Agent memory records.
- Preserve loading legacy `memory_record` payloads without evidence.
- Keep memory evidence policy in `coding_agent`, not AgentKit Core.
- Do not change memory rendering/injection authority in this goal.

Out of scope:
- AgentKit `MemoryRecord` directive schema changes.
- Memory context-pack rendering or omission of unevidenced memories; that is G23.
- External services or production credentials.

Context:
- ADR:
  - `docs/adr/0034-context-system-boundaries-and-evidence.md`
- Relevant files:
  - `src/coding_agent/plugins/memory.py`
  - `tests/coding_agent/plugins/test_memory.py`
- Postmortem:
  - `postmortem/patterns/PM-0009-preserve-neutral-bare-anchor-semantics.md`

Target tests:
- `uv run pytest tests/coding_agent/plugins/test_memory.py -k "evidence or Persistence" -v`
- `uv run pytest tests/coding_agent/plugins/test_memory.py -v`
- `uv run pytest tests/coding_agent/ -k "context_pack or retrieval or memory or evaluation" -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context" -v`

Loop policy:
- Engineer writes failing evidence/migration tests first.
- Engineer implements the smallest memory-plugin-only change.
- Reviewer reports only P1/P2 issues.
- Engineer fixes accepted P1/P2 findings and reruns target tests.

Stop conditions:
- Escalate if the change needs AgentKit runtime or directive schema changes.
- Escalate if JSONL legacy memory records cannot be loaded deterministically.
- Stop if memory rendering policy changes become necessary.
- Stop after two failed fix iterations for the same failure.
