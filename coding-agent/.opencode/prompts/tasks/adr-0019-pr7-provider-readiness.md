Goal:
Add ADR-0019 readiness coverage for configured cloud workspace providers so `/readyz` fails before traffic when the provider is misconfigured or unavailable.

Scope:
- Extend `/readyz` to include a `cloud_workspace` dependency check only when `cloud_workspace.enabled=true`.
- Add the minimal provider readiness hook needed to validate configured cloud workspace providers.
- Cover the HTTP readiness surface and the Docker provider readiness path with focused regression tests.

Out of scope:
- Do not change session creation, workspace transfer, or Docker workspace lifecycle semantics.
- Do not add new providers, live sync, or broader deployment docs in this slice.
- Do not refactor unrelated HTTP server or session-manager control flow.

Context:
- ADRs:
  - `docs/adr/0019-remote-client-cloud-workspace-deployment.md`
- Relevant files:
  - `src/coding_agent/ui/http_server.py`
  - `src/coding_agent/environment/workspace_provider.py`
  - `src/coding_agent/environment/docker_workspace_provider.py`
  - `tests/ui/test_http_server.py`
  - `tests/coding_agent/environment/test_docker_workspace_provider.py`
  - `tests/ui/test_execution_binding.py`

Target tests:
- `uv run pytest tests/ui/test_http_server.py -k readyz -v`
- `uv run pytest tests/coding_agent/environment/test_docker_workspace_provider.py -k readiness -v`
- `uv run basedpyright src/coding_agent/ui/http_server.py src/coding_agent/environment/workspace_provider.py src/coding_agent/environment/docker_workspace_provider.py tests/ui/test_http_server.py tests/coding_agent/environment/test_docker_workspace_provider.py tests/ui/test_execution_binding.py`

Loop policy:
- Engineer implements the smallest correct change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate architectural redirection or scope expansion to the human.
- Ignore non-blocking optimization suggestions.
