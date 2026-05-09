Goal:
Add explicit archive size and extracted-content limits to ADR-0019 workspace transfer so oversized uploads/downloads fail fast instead of consuming unbounded memory.

Scope:
- Enforce shared workspace archive byte limits in `coding_agent.workspace_archive` for both archive creation and extraction.
- Reject oversized snapshot uploads and oversized workspace exports through the existing HTTP surfaces.
- Cover the helper and HTTP transfer paths with focused regression tests.

Out of scope:
- Do not change archive format, owner fencing, or remote client protocol.
- Do not add streaming sync or chunked upload/download.
- Do not refactor unrelated Docker workspace provider or CLI workflow code.

Context:
- ADRs:
  - `docs/adr/0019-remote-client-cloud-workspace-deployment.md`
- Relevant files:
  - `src/coding_agent/workspace_archive.py`
  - `tests/coding_agent/environment/test_workspace_archive.py`
  - `tests/ui/test_http_server_workspace_transfer.py`

Target tests:
- `uv run pytest tests/coding_agent/environment/test_workspace_archive.py -v`
- `uv run pytest tests/ui/test_http_server_workspace_transfer.py -v`

Loop policy:
- Engineer implements the smallest correct change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate any protocol or API-shape changes beyond size-limit hardening.
- Ignore non-blocking optimization suggestions.
