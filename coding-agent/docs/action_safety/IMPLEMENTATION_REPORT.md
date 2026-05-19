# Action Safety And Workspace Execution Implementation Report

Date: 2026-05-19

## Summary

G25-G37 implemented the Action Safety + Workspace Execution phase as a bounded Coding Agent layer. The phase added current-state documentation, ADR-0035, patch planning, safe file edit validation, file patch dry-run/apply planning, command policy, validation runner contracts, metadata-only action observability, local workspace snapshot/restore, validation feedback context evidence, approval routing, and an end-to-end smoke test.

AgentKit Core remains generic. The implementation did not rewrite the AgentKit pipeline, change durable runtime semantics, remove JSONL compatibility, require external LLM calls, add production credentials, or implement schedules, desktop, bridge, proactive autonomous-agent behavior, or full Docker sandboxing.

## Landed Goals

| Goal | PR | Result |
| --- | --- | --- |
| G25 | #227 | Current-state audit, phase goal map, and ledger setup. |
| G26 | #228 | ADR-0035 action-safety and workspace-execution boundaries. |
| G27 | #229 | Patch planning data model and bounded risk classification. |
| G28 | #230 | Safe edit policy for workspace, symlink, size, binary, and create-target checks. |
| G29 | #231 | `file_patch` dry-run/preview validation and deterministic failure reporting. |
| G30 | #232 | Command policy decisions for allow, deny, approval, cwd/env/timeout, and path risks. |
| G31 | #233 | Validation runner specs and structured outcomes. |
| G32 | #234 | Metadata-only action observability spans/events. |
| G33 | #235 | Local workspace snapshot/restore MVP with manifest validation. |
| G34 | #236 | Validation feedback context adapter without raw output or authority changes. |
| G35 | #237 | Approval routing for command and file action policy results. |
| G36 | #238 | End-to-end safe action smoke coverage. |
| G37 | #239 | Final audit, report, and baseline verification. |

## Acceptance Audit

ADR-0035 acceptance criteria are satisfied by executable tests:

- `test_patch_plan_summarizes_hunks_and_risk_without_file_content`
- `test_safe_edit_policy_rejects_workspace_escape_symlink_binary_and_oversized_file`
- `test_file_patch_dry_run_reports_context_failure_without_mutation`
- `test_command_policy_classifies_allow_deny_and_approval_required`
- `test_command_policy_applies_to_local_and_cloud_shell_tools`
- `test_validation_runner_records_structured_outcomes_for_deterministic_commands`
- `test_action_observability_emits_safe_metadata_without_sensitive_attributes`
- `test_workspace_snapshot_restore_preserves_git_and_recovers_modified_files`
- `test_high_risk_file_or_command_action_routes_to_approval`
- `test_safe_action_smoke_covers_patch_command_validation_and_restore`

The final audit also covers the ADR-level verification commands:

- `uv run pytest tests/coding_agent/ -k "action_safety or safe_edit or patch_plan or command_policy or validation_runner or workspace_snapshot" -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`

## Safety Boundaries

- Safe summaries use bounded enums, counts, booleans, durations, labels, and metadata. Tests assert that raw file content, patch content, command strings, command output, prompts, messages, results, and text payloads are not emitted through the action-safety safe dictionaries or observability attributes covered by this phase. Labels remain caller-controlled safe labels, so callers must avoid embedding secrets or sensitive identifiers in labels.
- Validation feedback can be rendered as reference context evidence, but it does not change Context System authority semantics from ADR-0034.
- Snapshot restore validates the snapshot before clearing the workspace, rejects symlinks and preserved-root members, preserves `.git`, and rejects nested snapshot/workspace layouts.
- Approval routing distinguishes denied actions from approval-required actions and requires safe-edit decisions for file patch routing.

## Known Remaining Work

- Action-safety primitives are composed and tested, but most are not yet wired into every live autonomous tool execution path.
- Cloud `file_patch` and shell execution still depend on provider/client enforcement until a later execution-integration phase.
- The validation runner is local-only by design in this phase.
- Command policy remains conservative for path-bearing Python validation commands; G36 uses an existing deterministic allowlisted validation command for smoke coverage.
