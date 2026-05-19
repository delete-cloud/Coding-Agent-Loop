# G33 - Workspace Snapshot Restore

Add the G33 workspace snapshot/restore MVP for local temporary workspaces.

## Scope

- Keep snapshot/restore in `coding_agent.action_safety`.
- Provide explicit snapshot and restore operations instead of base64 transport as the primary contract.
- Preserve `.git` during restore.
- Reject symlinks, preserved-root members, unsafe snapshot roots, and manifest mismatches.
- Validate the snapshot fully before clearing the target workspace.

## Intended Files

- `src/coding_agent/action_safety/workspace_snapshot.py`
- `src/coding_agent/action_safety/__init__.py`
- `tests/coding_agent/action_safety/test_workspace_snapshot.py`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/action_safety/GOAL_PROGRESS.md`

## Postmortem Routing

G33 adds action-safety files and does not directly match existing `postmortem/index.yaml` `related_files`. Workspace archive safety tests remain relevant as regression context.

## Target Tests

- `uv run pytest tests/coding_agent/action_safety/test_workspace_snapshot.py tests/coding_agent/environment/test_workspace_archive.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_action_observability.py tests/coding_agent/action_safety/test_validation_runner.py tests/coding_agent/action_safety/test_command_policy.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `uv run ruff check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `git diff --check -- .`

## Stop Criteria

- Restore requires remote workspace live sync or Docker sandboxing.
- Restore cannot validate the snapshot before clearing the target workspace.
- Restore would overwrite or delete `.git`.
- More than two fix iterations fail for the same reason.
