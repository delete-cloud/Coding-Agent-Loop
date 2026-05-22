# ADR-0043: Define Bee command intent execution bridge boundaries

**Status**: Accepted
**Date**: 2026-05-22

## Context

ADR-0041 defines Bee as a generic Coding Agent task/workflow profile built on Topic and normal durable runs. ADR-0042 adds workspace-local `.bee` templates, sanitized run artifacts, and a non-executing `commands.yaml` command intent contract.

The next capability is a local safe bridge from Bee node command intent references into existing Coding Agent action safety and validation surfaces. The risk is turning `commands.yaml` into an executor manifest that bypasses approval, command policy, workspace policy, path policy, validation policy, HITL, or durable run/action records. A second risk is allowing a model response to mark a Bee node complete without evidence-producing acceptance checks.

## Decision

Keep the Bee command bridge in `src/coding_agent/` as product-layer code. AgentKit Core remains generic and must not gain Bee command primitives.

Define `command_ref` as a declarative reference:

- A Bee node may reference a workspace command intent by safe name.
- `command_ref` does not carry a command string.
- `command_ref` does not grant execution rights.
- `command_ref` may appear in manifests, durable Bee records, task artifacts, console routes, and safe trace correlation attributes.
- `command_ref` must not become a Prometheus label.

Define `commands.yaml` as a policy-bound intent source:

- `commands.yaml` may declare safe intent metadata: name, profile, policy hint, category, validation label, status, and bounded safe metadata.
- `commands.yaml` must not contain executable command strings, shell snippets, args, env, stdout/stderr, command output, secrets, or raw text.
- `commands.yaml` cannot override command policy or workspace policy.
- Disabled intents must not execute.

Define bridge execution as a gated local action:

- A bridge request resolves `task_id`, `node_id`, `template_id`, `command_ref`, workspace root or lease, and command intent.
- The bridge must evaluate existing command policy before execution.
- The bridge must route approval-required decisions into existing HITL/approval behavior or return an approval-required result without executing.
- The bridge must fail closed when workspace binding, command policy, approval, path policy, validation policy, or evidence requirements are missing.
- The bridge must be deterministic in tests with fake command executors, fake validation outputs, fake clocks, and temp workspaces.

Define validation behavior:

- Validation node kinds should use the existing `ValidationRunner` and `ValidationCommandSpec` path where command execution is required.
- Validation results must be evidence-backed.
- A Bee node cannot complete from model text alone.
- A Bee node completes only when acceptance criteria produce evidence references such as validation result IDs, action IDs, report entries, or sanitized artifact paths.

Define artifacts and durable records:

- Bridge results may write sanitized `.bee/runs/<task>/task.json`, `report.md`, evidence references, action IDs, validation IDs, and memory candidate metadata.
- Raw prompt/content/message/result/secret/text/command output/stdout/stderr/env must not be stored in traces, metrics, durable records, task artifacts, reports, evidence, memory candidates, or console pages.
- `task.json` remains a sanitized mirror, not authoritative state.

Define observability and console boundaries:

- Metrics may use low-cardinality labels such as task kind/profile/status, node kind/profile/status, command category/policy/status, action kind/status, and validation status.
- Metrics must not use `task_id`, `topic_id`, `run_id`, `session_id`, `node_id`, `file_path`, command strings, prompt, content, or secret labels.
- Console pages may show safe IDs, command refs, intent names, status, policy decisions, approval-required status, validation status, and evidence references.
- Console pages must not render raw command output, stdout/stderr, env, prompts, messages, result text, secrets, or raw evidence body.

Keep out of scope:

- Homelab-specific templates or hard-coded NetBird, OCI, Argo CD, nmem, Kubernetes, or backup behavior.
- External executor, Argo Workflows, Kubernetes Jobs, Docker-only execution, desktop app, bridge app, and multi-agent task graph runtime.
- Production credentials, hosted services, real external LLM calls, and production-changing commands.

## Alternatives Rejected

- Execute command strings from `commands.yaml`. Rejected because `commands.yaml` is declarative intent and must not grant execution rights.
- Let Bee runtime complete nodes from model text. Rejected because node completion must be evidence-backed.
- Add command bridge primitives to AgentKit Core. Rejected because Bee is a Coding Agent product profile.
- Make command policy configurable from `.bee` templates. Rejected because templates cannot override safety policy.
- Store raw command output in artifacts for debugging. Rejected because it violates the no-leak contract.
- Use task/node/run/session IDs as Prometheus labels. Rejected because they are high-cardinality identifiers.

## Acceptance Criteria

- [x] `test_bee_node_manifest_accepts_safe_command_ref`
- [x] `test_bee_manifest_rejects_executable_fields`
- [x] `test_bee_command_bridge_resolves_declared_intent_without_executing_yaml`
- [x] `test_bee_command_bridge_denies_policy_blocked_intent`
- [x] `test_bee_command_bridge_returns_approval_required_without_execution`
- [x] `test_bee_validation_node_uses_validation_runner`
- [ ] `test_bee_node_completion_requires_evidence`
- [ ] `test_console_bee_command_bridge_renders_safe_execution_summary`
- [ ] `test_bee_command_bridge_metrics_omit_high_cardinality_ids`
- [ ] `test_bee_command_bridge_smoke`
- [ ] `uv run pytest tests/coding_agent/test_bee_runtime.py -v`
- [ ] `uv run pytest tests/coding_agent/test_bee_workspace.py -v`
- [ ] `uv run pytest tests/coding_agent/test_bee_command_bridge.py -v`
- [ ] `uv run pytest tests/ui/test_developer_console.py -v`
- [ ] `git diff --check -- .`

## References

- `docs/bee_command_bridge/CURRENT_STATE.md`
- `docs/bee_command_bridge/GOAL_PROGRESS.md`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/adr/0036-observability-backend-exporter-boundaries.md`
- `docs/adr/0037-developer-console-debug-ui.md`
- `docs/adr/0038-workspace-provider-and-sandbox-mvp-boundaries.md`
- `docs/adr/0041-bee-workflow-task-runtime.md`
- `docs/adr/0042-bee-workspace-contract.md`
- `src/coding_agent/bee_runtime.py`
- `src/coding_agent/bee_workspace.py`
- `src/coding_agent/action_safety/command_policy.py`
- `src/coding_agent/action_safety/validation_runner.py`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/ui/developer_console.py`
- `src/coding_agent/observability.py`
