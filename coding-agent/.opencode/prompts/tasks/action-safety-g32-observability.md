# G32 - Action Observability

Add action-safety observability spans/events with safe metadata only.

## Scope

- Keep action observability in `coding_agent.action_safety`; AgentKit remains the generic sink/span provider.
- Emit typed action metadata for file edits, patches, commands, validation, and restore actions.
- Allow only bounded labels, counts, booleans, durations, exit codes, and enum-like status fields.
- Reject raw command strings, raw output, file content, env values, prompts, messages, results, secrets, and free-form text.

## Intended Files

- `src/coding_agent/action_safety/action_observability.py`
- `src/coding_agent/action_safety/__init__.py`
- `tests/coding_agent/action_safety/test_action_observability.py`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/action_safety/GOAL_PROGRESS.md`

## Postmortem Routing

G32 adds action-safety files and does not directly match existing `postmortem/index.yaml` `related_files`.

## Target Tests

- `uv run pytest tests/coding_agent/action_safety/test_action_observability.py tests/coding_agent/test_observability.py tests/agentkit/observability/test_core.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_validation_runner.py tests/coding_agent/action_safety/test_command_policy.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `uv run ruff check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `git diff --check -- .`

## Stop Criteria

- Implementation requires AgentKit observability model changes.
- Safe attributes require raw prompts, content, messages, results, secrets, command output, file content, env values, or free-form text.
- Implementation requires wiring action observability into live tool execution paths before G35/G36.
- More than two fix iterations fail for the same reason.
