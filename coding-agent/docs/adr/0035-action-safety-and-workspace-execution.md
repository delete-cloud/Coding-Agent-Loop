# ADR-0035: Bound action safety and workspace execution policy

**Status**: Proposed
**Date**: 2026-05-19

## Context

The next phase adds safe action execution: patch planning, safe file edits, command execution policy, validation/test runner behavior, action observability, approval routing, and workspace snapshot/restore. The current code already has AgentKit tool registration and pipeline hooks, Coding Agent local and cloud environments, local file and shell tools, a cloud workspace client abstraction, tool-name approval policy, and workspace archive helpers.

The main design risk is boundary drift. File paths, patch risk, shell command policy, validation command selection, approval prompts, and workspace restore behavior are Coding Agent product semantics. AgentKit should remain a generic runtime and tool execution framework; it should not learn repository file safety rules, pytest conventions, cloud provider behavior, or product approval categories.

The second design risk is false safety parity. Local execution and cloud execution expose the same model-facing tool names, but they do not share the same implementation. Local `bash_run` rejects shell metacharacters and validates local path arguments. Cloud `bash_run` validates cwd and delegates the raw command string to `CloudWorkspaceClient.run_command`. This phase must define policy above both environments instead of assuming the local implementation protects every execution path.

## Decision

Keep action-safety policy in `coding_agent` and keep AgentKit generic:

- AgentKit continues to own generic tool schemas, tool execution hooks, runtime spans, and pipeline safe points.
- Coding Agent owns file edit policy, patch planning, command policy, validation command specs, approval risk categories, local/cloud policy adaptation, workspace snapshot/restore, and user-facing action summaries.
- Product policy modules should live under `coding_agent` unless a later reusable abstraction is demonstrably environment-neutral.
- The AgentKit pipeline must not be rewritten for this phase. New behavior should compose through existing environment, tool, approval, and observability boundaries.

Represent file changes as plans before mutation:

- A patch plan is an app-level, JSON-safe structure that summarizes target paths, operations, hunks, added/deleted line counts, file existence, and risk.
- Plans classify risk using bounded enum values, not free-form model text.
- Safe edit validation must cover workspace boundary, path normalization, symlinks, file size, binary detection, missing files, and expected context.
- Patch application should support dry-run/preview validation before mutation and deterministic failure reporting when context, path, or policy checks fail.
- Multi-file transactions are not required for this phase.

Represent command execution policy explicitly:

- A command policy decision is an app-level result with `allow`, `deny`, or `approval_required`, plus bounded reason codes.
- Policy must account for command name, shell syntax, cwd, env keys, timeout, path escape risk, destructive operations, network/process risk where locally visible, and validation-only commands.
- Local and cloud environments may enforce different low-level guards, but both must be governed by the same policy contract before execution.
- Validation commands are still commands. They may be allowlisted by deterministic specs, but they must not bypass policy, timeout, cwd, env, or observability rules.

Add a validation runner contract instead of ad hoc command strings:

- A validation command spec includes a stable label, command argv or command string according to the selected executor, cwd, env, timeout, and expected success condition.
- A validation outcome records label, status, exit code when available, duration, and bounded failure summary metadata.
- Raw stdout/stderr may be returned to the local caller when already part of tool behavior, but observability attributes must not include raw command output.
- Validation feedback may be surfaced to the Context System as reference evidence after G34, without changing context-pack authority semantics from ADR-0034.

Keep action observability metadata-only:

- Action spans/events belong in Coding Agent and may use existing AgentKit/Coding Agent observability sinks.
- Safe attributes include action kind, policy decision, bounded reason codes, risk level, counts, booleans, durations, path counts, file extension buckets, command labels, exit code, and restore status.
- Attribute keys and values must avoid raw prompts, content, messages, command output, file content, tool arguments, tool results, secrets, environment values, and other sensitive text.
- Attribute keys must avoid sensitive substrings already filtered by the exporter, including `content`, `message`, `prompt`, `result`, `secret`, and `text`.

Provide workspace snapshot/restore as a local MVP:

- The first restore primitive is local and temporary-workspace focused.
- It may reuse workspace archive safety lessons, but should expose explicit snapshot and restore operations instead of base64 transport as the primary app contract.
- Restore must preserve `.git`, reject unsafe archive members or symlinks, and fail without clearing the target when snapshot validation fails.
- Full Docker sandboxing, remote live sync, CRDT merging, and multi-user concurrent workspace reconciliation are non-goals for this phase.

## Alternatives Rejected

- Move file and command policy into AgentKit Core. Rejected because repository paths, shell conventions, validation commands, approval categories, and cloud provider behavior are product-specific.
- Rely on local `bash_run` parsing as the global safety boundary. Rejected because cloud execution uses a separate implementation that delegates raw commands to a client.
- Let validation commands bypass approval and policy. Rejected because tests and linters can still mutate files, access networks, hang, or leak environment details.
- Emit raw commands, file paths with sensitive names, stdout/stderr, patch contents, or file content in trace attributes. Rejected because observability must remain metadata-only.
- Implement full Docker sandboxing or remote workspace merge semantics in this phase. Rejected because this phase is scoped to safe action policy and a local snapshot/restore MVP.
- Add multi-file atomic patch transactions now. Rejected because the immediate goal is deterministic planning, preview, policy, and failure reporting; transactions can be evaluated after the single-action contract is stable.

## Acceptance Criteria

Implementation of G27-G37 should add executable tests covering these contracts:

- [x] `test_patch_plan_summarizes_hunks_and_risk_without_file_content`
- [x] `test_safe_edit_policy_rejects_workspace_escape_symlink_binary_and_oversized_file`
- [x] `test_file_patch_dry_run_reports_context_failure_without_mutation`
- [x] `test_command_policy_classifies_allow_deny_and_approval_required`
- [x] `test_command_policy_applies_to_local_and_cloud_shell_tools`
- [x] `test_validation_runner_records_structured_outcomes_for_deterministic_commands`
- [x] `test_action_observability_emits_safe_metadata_without_sensitive_attributes`
- [x] `test_workspace_snapshot_restore_preserves_git_and_recovers_modified_files`
- [x] `test_high_risk_file_or_command_action_routes_to_approval`
- [x] `test_safe_action_smoke_covers_patch_command_validation_and_restore`
- [ ] `uv run pytest tests/coding_agent/ -k "action_safety or safe_edit or patch_plan or command_policy or validation_runner or workspace_snapshot" -v`
- [ ] `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`

## References

- `docs/action_safety/CURRENT_STATE.md`
- `docs/action_safety/GOAL_PROGRESS.md`
- `docs/adr/0016-stage-environment-runtime-and-tool-orchestration-prs.md`
- `docs/adr/0017-cloud-workspace-execution.md`
- `docs/adr/0022-runtime-profiles-and-sandbox-policy.md`
- `docs/adr/0028-observability-and-langfuse-integration.md`
- `docs/adr/0034-context-system-boundaries-and-evidence.md`
- `src/coding_agent/environment/local.py`
- `src/coding_agent/environment/cloud.py`
- `src/coding_agent/tools/file_ops.py`
- `src/coding_agent/tools/file_patch_tool.py`
- `src/coding_agent/tools/shell.py`
- `src/coding_agent/tools/sandbox.py`
- `src/coding_agent/approval/policy.py`
- `src/coding_agent/plugins/core_tools.py`
- `src/coding_agent/workspace_archive.py`
