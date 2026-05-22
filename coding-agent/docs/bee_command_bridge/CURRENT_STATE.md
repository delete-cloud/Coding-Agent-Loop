# Bee Command Bridge Current State

## Scope

This phase connects Bee workspace command intents to existing local action and validation safety surfaces. It must remain generic Coding Agent product code and must not add homelab-specific templates, external executors, hosted services, Docker/Kubernetes/Argo/nmem requirements, desktop, bridge, or multi-agent infrastructure.

## Current Bee Runtime

- `src/coding_agent/bee_runtime.py` defines generic Bee task records, node records, manifests, planner launch intents, topic anchors, and safe launch metadata.
- `BeeTaskManifest` and `BeeNodeManifest` validate safe labels, metadata, dependencies, context profiles, validation profiles, and workspace policy references.
- `BeeTaskPlanner.plan_ready_nodes()` returns bounded `BeeNodeLaunchIntent` objects; it claims ready nodes but does not execute commands.
- `bee_launch_metadata()` creates safe metadata for normal durable runs:
  - `bee_runtime=task_launch`
  - task/node/session/topic identifiers
  - task/node kind/profile/status references
  - `approval_policy=existing_runtime_policy`
  - `action_policy=existing_action_safety`
  - `workspace_binding=existing_workspace_provider`
  - optional workspace, context, and validation profile references
- Existing runtime tests prove Bee launch metadata is additive and reference-only, not an executor.

## Current Bee Workspace Contract

- `src/coding_agent/bee_workspace.py` discovers `.bee/templates/<template_id>/` and validates `metadata.yaml` or `metadata.json` through the existing Bee manifest parser.
- `commands.yaml` parsing is intentionally non-executing:
  - `BeeWorkspaceCommandIntent` carries `name`, `profile`, `policy`, `category`, optional `validation_label`, `status`, and safe metadata.
  - Executable fields such as `command`, `commands`, `cmd`, `args`, `argv`, `shell`, `script`, `exec`, and `executor` are rejected.
  - Sensitive/raw fields such as prompt/content/message/result/secret/text/command output/stdout/stderr/env/token/key/credential are rejected.
- `.bee/runs/<task>/task.json` and `report.md` are sanitized mirrors of durable state, not authoritative task state.
- No current code resolves a Bee node `command_ref` to a command intent.
- No current code executes a command intent or writes action/validation evidence for a Bee node.

## Current Action Safety And Validation Surfaces

- `src/coding_agent/action_safety/command_policy.py` provides `evaluate_command_policy()`, `CommandPolicyVerdict`, and allow/deny/approval-required decisions.
- `src/coding_agent/action_safety/approval_routing.py` maps command policy verdicts to allow, deny, or approval-required routes.
- `src/coding_agent/action_safety/action_observability.py` emits safe low-cardinality action observations without raw command output.
- `src/coding_agent/action_safety/validation_runner.py` executes `ValidationCommandSpec` through existing command policy checks:
  - validation commands are policy-checked with `validation_command=True`
  - denied commands do not execute
  - approval-required commands return an approval-required result and do not execute
  - successful execution returns parsed status, exit code, and bounded result references
- `src/coding_agent/action_safety/safe_edit.py`, `workspace_snapshot.py`, and command policy/path helpers are available for existing workspace safety, but there is no Bee-specific bridge yet.

## Current Workspace Provider Surface

- Workspace provider code lives under `src/coding_agent/environment/`.
- `cloud.py` and `docker_workspace_provider.py` expose command-running capability behind provider abstractions, but this phase must not require Docker or hosted providers.
- Existing local tests prefer fake providers, temp directories, and deterministic command outputs.
- The command bridge should accept an explicit local workspace root or provider lease abstraction and should be testable with fake command executors.

## Current HITL / Approval Surface

- Runtime approval flow is handled by `src/coding_agent/ui/session_manager.py` and durable interaction records.
- Console HITL pages read `AgentInteractionRecord` and render pending/resolved approvals safely.
- Existing approval flow is coupled to normal durable runs and wire consumers; a Bee local bridge should not bypass it.
- For MVP, approval-required command intents can produce a blocked/approval-required execution result rather than auto-approving.

## Current Console And Observability

- `/console/bee` renders:
  - durable Bee task summaries
  - Bee node launch summaries
  - workspace template summaries
  - workspace run artifact summaries
  - non-executing command intent summaries
- Workspace-backed Bee console sections are visible only to admin or local no-token contexts.
- Prometheus allows low-cardinality Bee labels:
  - task kind/profile/status
  - node kind/profile/status
  - template kind/profile
  - command category/policy/status
- Prometheus forbids high-cardinality labels including task, node, run, session, topic, template, file path, command, prompt, content, and secret identifiers.

## Current Tests To Preserve

- `tests/coding_agent/test_bee_runtime.py`
- `tests/coding_agent/test_bee_workspace.py`
- `tests/coding_agent/action_safety/test_command_policy.py`
- `tests/coding_agent/action_safety/test_validation_runner.py`
- `tests/coding_agent/action_safety/test_safe_action_smoke.py`
- `tests/ui/test_developer_console.py`
- `tests/coding_agent/test_observability.py`
- `tests/coding_agent/test_observability_platform_smoke.py`
- Release verification gates in `docs/release_hardening/release-verification.yaml`

## Gaps For G111-G117

- No ADR yet defines Bee command bridge boundaries.
- `BeeNodeManifest` does not currently expose `command_ref`.
- `commands.yaml` intentionally lacks executable command strings. A bridge must resolve only declarative command intents and must not treat `commands.yaml` as an execution grant.
- No `BeeCommandBridge` or local safe executor exists.
- No bridge result model exists for:
  - resolved intent
  - command policy verdict
  - approval required
  - validation result
  - evidence references
  - sanitized report updates
- No deterministic fake executor coverage exists for Bee command intent execution.
- No console section shows Bee command bridge execution results yet.
- No final smoke exists showing a Bee node cannot complete from model text alone and completes only from evidence-producing acceptance checks.

## Likely Files To Modify Later

- `docs/bee_command_bridge/GOAL_PROGRESS.md`
- `docs/bee_command_bridge/CURRENT_STATE.md`
- `docs/bee_command_bridge/IMPLEMENTATION_REPORT.md`
- `docs/bee_command_bridge/USAGE.md`
- `docs/adr/0043-bee-command-bridge.md`
- `src/coding_agent/bee_runtime.py`
- `src/coding_agent/bee_workspace.py`
- new `src/coding_agent/bee_command_bridge.py`
- `src/coding_agent/observability.py`
- `src/coding_agent/ui/developer_console.py`
- `src/coding_agent/ui/http_server.py`
- new or updated tests under `tests/coding_agent/` and `tests/ui/`

## Current Boundary Conclusion

The repository has the safe declarative Bee and workspace contracts needed to start this phase. The missing piece is a product-layer bridge that resolves a Bee node command reference into an already-declared command intent, then routes that intent through existing policy, approval, validation, and evidence mechanisms. The bridge must remain local, deterministic, fake-executor-testable, and fail closed when a policy, workspace, approval, or evidence requirement is missing.
