# G34 - Validation Feedback Context

Add validation feedback integration after edits and commands without changing context-system semantics.

## Scope

- Convert structured validation outcomes into evidence-backed `ContextPack` reference grounding.
- Keep the integration in `coding_agent.action_safety`; do not change AgentKit context building or ADR-0034 authority rules.
- Surface failed validation status, exit code, duration, policy decision, and bounded failure metadata.
- Do not render raw commands, stdout/stderr, environment values, file content, prompts, messages, results, secrets, or free-form output.

## Intended Files

- `src/coding_agent/action_safety/validation_feedback.py`
- `src/coding_agent/action_safety/__init__.py`
- `tests/coding_agent/action_safety/test_validation_feedback.py`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/action_safety/GOAL_PROGRESS.md`

## Postmortem Routing

G34 adds action-safety files and uses the existing context-pack model without changing context-system plugin behavior.

## Target Tests

- `uv run pytest tests/coding_agent/action_safety/test_validation_feedback.py tests/coding_agent/test_context_pack.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_validation_runner.py tests/coding_agent/action_safety/test_command_policy.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `uv run ruff check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `git diff --check -- .`

## Stop Criteria

- Integration requires changing AgentKit pipeline/context stages.
- Integration changes context-pack authority semantics from ADR-0034.
- Feedback requires raw command output, raw commands, env values, prompts, messages, results, secrets, or file content.
- More than two fix iterations fail for the same reason.
