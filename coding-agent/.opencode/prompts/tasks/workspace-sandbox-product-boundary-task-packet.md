Goal:
Clarify Workspace as the execution boundary across local repositories, Docker
sandbox workspaces, future OS-native sandboxes, cloud workspaces, and external
executor workspace references.

Scope:
- Document and enforce product terminology around `ExecutionBinding`,
  `Environment`, and `WorkspaceProvider`.
- Keep session metadata responsible for workspace binding, not for filesystem or
  shell implementation details.
- Prepare executor-related code to sit behind an execution-plane boundary while
  preserving existing compatibility paths.

Out of scope:
- Implementing OS-native sandbox support.
- Rewriting Docker provider behavior.
- Renaming public remote HTTP protocol fields in this slice.
- Changing action-safety approval policy.

Context:
- ADRs:
  - `docs/adr/0038-workspace-provider-and-sandbox-mvp-boundaries.md`
  - `docs/adr/0051-external-worker-execution-control-plane.md`
  - `docs/adr/0054-executor-runtime-terminology-and-resume-first-direction.md`
  - `docs/adr/0056-local-cli-control-plane-and-workspace-product-boundaries.md`
- Relevant files:
  - `src/coding_agent/server/execution_binding.py`
  - `src/coding_agent/environment/local.py`
  - `src/coding_agent/environment/cloud.py`
  - `src/coding_agent/environment/workspace_provider.py`
  - `src/coding_agent/environment/docker_workspace_provider.py`
  - `src/coding_agent/server/session_manager.py`
  - `src/coding_agent/external_executor.py`
  - `tests/ui/test_execution_binding.py`
  - `tests/coding_agent/environment/`

Target tests:
- `uv run pytest tests/ui/test_execution_binding.py -v`
- `uv run pytest tests/coding_agent/environment/test_local_environment.py tests/coding_agent/environment/test_cloud_environment.py -v`
- `uv run pytest tests/coding_agent/environment/test_docker_workspace_provider.py -k "binding or unavailable or provider_instance" -v`
- `uv run pytest tests/coding_agent/test_external_executor.py -k "capability or authorized or workspace" -v`

Loop policy:
- Engineer implements the smallest correct change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate if the slice would require a protocol migration.
- Escalate if Docker or hosted infrastructure becomes required for deterministic tests.
- Ignore non-blocking optimization suggestions.
