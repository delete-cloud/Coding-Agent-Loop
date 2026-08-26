# ADR-0038: Workspace provider and sandbox MVP boundaries

**Status**: Accepted
**Date**: 2026-05-21

## Context

G00-G67 established durable runtime state, context evidence, action safety,
release contracts, observability, the Developer Console, and dogfood/demo
readiness. The next phase makes workspace providers explicit and finishes a
sandbox MVP. The repository already has substantial app-layer workspace code:
`ExecutionBinding`, local and cloud environments, a `WorkspaceProvider`
protocol, a provider registry, Docker-backed provider code, workspace lifecycle
HTTP routes, archive/diff/patch/publish helpers, and deterministic fake-provider
tests.

The design risk is duplicate abstraction and boundary drift. AgentKit Core
should remain a generic runtime/framework layer. Coding Agent owns product
workspace semantics: local repository paths, Docker/provider wiring, cloud
client factories, workspace lifecycle policy, action-safety routing, console
display, and release/demo documentation.

The second risk is treating sandbox support as all-or-nothing Docker
infrastructure. Docker is useful as an optional workspace provider, but local
tests and local development must not require a Docker daemon, production
credentials, or hosted services.

## Decision

Keep AgentKit Core provider-neutral. AgentKit may depend on generic environment
and tool protocols, but it must not import Coding Agent workspace providers,
Docker code, cloud clients, console views, or provider-specific configuration.

Coding Agent owns workspace provider wiring and sandbox MVP behavior:

- `ExecutionBinding` remains the durable/session representation of the selected
  workspace execution target.
- Local execution continues to use `LocalExecutionBinding` and
  `LocalEnvironment`.
- Remote or sandbox execution uses explicit provider metadata, workspace ids,
  provider instance ids, and provider-backed clients through Coding Agent
  environment modules.
- `WorkspaceProvider` remains an app-layer provider contract for readiness,
  provisioning, cleanup, inventory, archive, diff, patch, and publish behavior.
- Docker is an optional provider implementation with deterministic fake-runner
  tests. Capability detection and graceful fallback are required before using
  Docker-specific behavior.
- Existing action-safety policies continue to govern file, patch, shell,
  validation, approval, and restore behavior regardless of provider.

The phase should harden the existing local/cloud/Docker ownership boundary
instead of renaming or rewriting broad surfaces. New descriptors or helper
types are acceptable only when they clarify provider ids, capability detection,
or safe metadata transfer without changing G00-G67 behavior.

Provider-local operations must fail closed when the durable workspace
`provider_instance_id` does not match the configured local provider instance.
Non-local workspace records may be listed as remote or unavailable metadata, but
cleanup, archive export, diff, patch, publish, retention changes, and deletion
must not interpret host paths or provider resource ids from another provider
instance.

Workspace lifecycle APIs preserve the ADR-0021 session/admin boundary.
Session-scoped workspace operations are allowed only when ownership is proven
through the session. Workspace-scoped inspection, archive, cleanup, retain,
pin, unpin, delete, and global GC operations are administrative unless the
server can prove that the workspace belongs to the caller's session.

Workspace provider metadata may be recorded in durable records and rendered in
console pages when it is part of an existing sanitized API contract or a new
explicitly safe contract. Safe fields include bounded provider kind, provider
instance id, workspace status, timestamps, lifecycle state, capability names,
counts, booleans, and safe correlation ids needed for operator debugging.

Privacy and observability rules are mandatory:

- Do not store or render raw prompt, content, message, model result text, file
  contents, patch contents, command output, stdout, stderr, environment values,
  credentials, tokens, or secrets in durable records, traces, metrics, docs, or
  console pages.
- Do not add raw sensitive data to tracing attributes while routing workspace
  actions.
- Prometheus labels must remain low-cardinality. Forbidden labels include
  `run_id`, `session_id`, `workspace_id`, `file_path`, `command`, `prompt`,
  `content`, and `secret`.
- Metrics failures must not break runtime or workspace cleanup behavior.

The Developer Console may add workspace provider visibility in this phase, but
it must remain a safe debug surface over existing APIs and must not bypass
approval, command policy, validation policy, or workspace action policy.

## Alternatives Rejected

- **Move workspace provider abstractions into AgentKit Core**. Rejected because
  provider wiring, repository paths, Docker configuration, cloud client
  factories, and workspace lifecycle policy are Coding Agent product concerns.
- **Rewrite execution around a new provider model**. Rejected because existing
  `ExecutionBinding`, environment, provider, HTTP, and test surfaces already
  provide the needed base; broad churn would risk G00-G67 regressions.
- **Require Docker for all sandbox tests**. Rejected because the phase must be
  deterministic on machines without Docker and the current provider can be
  tested through fake command runners and temp directories.
- **Treat Docker as a production credential or hosted dependency**. Rejected
  because the MVP is local/optional and must not require external hosted
  services.
- **Expose raw workspace contents for better debugging**. Rejected because the
  project privacy model forbids raw prompt/content/message/result/secret/text,
  file content, patch content, command output, stdout, stderr, and environment
  values in durable metadata, traces, metrics, docs, or console pages.
- **Use workspace/session/run/file ids as Prometheus labels**. Rejected because
  those labels are high-cardinality and violate ADR-0036.
- **Add schedule, desktop, bridge, proactive-agent, or multi-agent graph work to
  complete the sandbox MVP**. Rejected because those are later phases and not
  required for explicit workspace providers.

## Acceptance Criteria

- [ ] `test_execution_binding_round_trips_explicit_workspace_provider_metadata`
- [ ] `test_local_workspace_provider_uses_temp_workspace_without_docker`
- [ ] `test_action_tools_route_through_selected_workspace_binding`
- [ ] `test_docker_workspace_provider_reports_unavailable_without_required_capability`
- [ ] `test_workspace_lifecycle_routes_render_safe_metadata_only`
- [ ] `test_workspace_provider_local_operations_fail_closed_for_foreign_provider_instance`
- [ ] `test_workspace_lifecycle_routes_require_session_ownership_or_admin_scope`
- [ ] `test_console_workspace_provider_view_omits_raw_content_and_secrets`
- [ ] `test_workspace_metrics_use_low_cardinality_labels`
- [ ] `test_workspace_provider_demo_uses_deterministic_local_or_fake_provider`
- [ ] `uv run pytest tests/ui/test_execution_binding.py -v`
- [ ] `uv run pytest tests/coding_agent/environment/test_docker_workspace_provider.py -v`
- [ ] `uv run pytest tests/ui/test_http_server_workspace_transfer.py -v`
- [ ] `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- [ ] `uv run pytest tests/ui/test_developer_console.py -v`
- [ ] `git diff --check -- .`

## References

- `docs/workspace_provider/CURRENT_STATE.md`
- `docs/workspace_provider/GOAL_PROGRESS.md`
- `docs/adr/0021-remote-session-and-workspace-operations-api.md`
- `docs/adr/0022-runtime-profiles-and-sandbox-policy.md`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/adr/0036-observability-backend-exporter-boundaries.md`
- `docs/adr/0037-developer-console-debug-ui.md`
- `src/agentkit/environment/protocols.py`
- `src/coding_agent/environment/local.py`
- `src/coding_agent/environment/cloud.py`
- `src/coding_agent/environment/workspace_provider.py`
- `src/coding_agent/environment/docker_workspace_provider.py`
- `src/coding_agent/ui/execution_binding.py`
- `src/coding_agent/ui/binding_resolver.py`
- `src/coding_agent/ui/session_manager.py`
- `src/coding_agent/ui/http_server.py`
- `tests/ui/test_execution_binding.py`
- `tests/coding_agent/environment/test_docker_workspace_provider.py`
- `tests/ui/test_http_server_workspace_transfer.py`
