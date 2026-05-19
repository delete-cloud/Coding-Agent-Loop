Goal:
Add repo-aware source and chunk metadata to the KB indexing model.

Scope:
- Extend `KB.index_file` and `KB.index_directory` to record JSON-safe repo-aware metadata for indexed chunks.
- Add tests for source kind, repo-relative path, language, line range, source id, and document/chunk hashes.
- Keep AgentKit Core untouched and preserve existing KB search behavior.
- Update `docs/context_system/GOAL_PROGRESS.md` with G14 before/after evidence.

Out of scope:
- Context-pack rendering.
- Retrieval query/scoring changes.
- Testing-failure retrieval.
- Retrieval observability spans.
- Memory evidence persistence.
- Durable runtime changes.

Context:
- ADRs:
  - `docs/adr/0034-context-system-boundaries-and-evidence.md`
- Postmortems:
  - `postmortem/patterns/PM-0009-preserve-neutral-bare-anchor-semantics.md`
- Relevant files:
  - `src/coding_agent/kb.py`
  - `tests/test_kb.py`
  - `tests/coding_agent/test_kb_sync.py`
  - `tests/cli/test_kb_commands.py`
  - `docs/context_system/GOAL_PROGRESS.md`

Target tests:
- `uv run pytest tests/test_kb.py -k "repo_chunk_metadata_records_source_kind_and_repo_path or index_directory_records_repo_relative_metadata or index_file_rejects_path_outside_repo_root" -v`
- `uv run pytest tests/test_kb.py tests/coding_agent/test_kb_sync.py tests/cli/test_kb_commands.py -v`
- `uv run pytest tests/coding_agent/plugins/test_kb.py tests/coding_agent/plugins/test_kb_plugin.py -v`

Loop policy:
- Engineer writes failing tests first, then implements the smallest correct change.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- More than two fix iterations fail for the same reason.
- The change requires rewriting AgentKit pipeline behavior.
- The change requires durable runtime semantic changes.
- The change requires external services or production credentials.
