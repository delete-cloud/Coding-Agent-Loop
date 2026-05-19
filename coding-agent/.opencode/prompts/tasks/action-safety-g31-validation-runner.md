# G31 - Validation Runner Contract

Add the G31 validation/test runner contract with deterministic command specs and structured outcomes.

## Scope

- Keep the contract in `coding_agent.action_safety`; do not move product policy into AgentKit.
- Reuse the G30 command policy before executing validation commands.
- Record structured outcomes with stable labels, status, exit code, duration, policy metadata, and bounded failure metadata.
- Do not put raw stdout/stderr, command output, environment values, or file content into safe outcome dictionaries.
- Keep existing `coding_agent.verification` behavior intact unless direct integration becomes necessary.
- Deny absolute executable paths outside the workspace before validation execution.

## Intended Files

- `src/coding_agent/action_safety/validation_runner.py`
- `src/coding_agent/action_safety/command_policy.py`
- `src/coding_agent/action_safety/__init__.py`
- `tests/coding_agent/action_safety/test_validation_runner.py`
- `tests/coding_agent/action_safety/test_command_policy.py`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/action_safety/GOAL_PROGRESS.md`

## Postmortem Routing

G31 adds action-safety files and does not directly match existing `postmortem/index.yaml` `related_files`.

## Target Tests

- `uv run pytest tests/coding_agent/action_safety/test_validation_runner.py tests/coding_agent/action_safety/test_command_policy.py -v`
- `uv run pytest tests/coding_agent/test_verification.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `uv run ruff check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `git diff --check -- .`

## Stop Criteria

- Validation runner requires modifying AgentKit pipeline stages.
- Validation runner requires context-system authority or evidence semantics changes.
- Outcomes require raw command output, raw env values, or raw tool arguments in safe summaries.
- More than two fix iterations fail for the same reason.
