# Bee Runtime Usage

Bee is a generic Coding Agent task/workflow profile built on Topic. It is not an external executor and it does not bypass durable runs, HITL, approval policy, command policy, workspace policy, path policy, validation policy, or action safety.

## Safe Manifest Shape

A Bee manifest describes product intent:

- `version`: currently `1`
- `kind` and `profile`: low-cardinality labels such as `maintenance` and `local`
- `title` and optional `summary`: bounded safe display text
- `topic`: existing `session_id` plus optional `topic_id` and `tape_id`
- `context_profile`, `validation_profile`, and `workspace_policy`: profile references only
- `nodes`: ordered node records with `node_id`, `kind`, `profile`, `title`, optional dependencies, and optional profile references

Manifests must not include raw prompt, content, message, result, secret, text, command output, stdout, stderr, env, command, shell, script, executor, or arbitrary executable fields.

## Runtime Flow

1. Parse and sanitize the manifest with `parse_bee_task_manifest()`.
2. Persist `BeeTaskRecord` and `BeeNodeRecord` through `PGBeeTaskStore`.
3. Write safe task lifecycle anchors with `BeeTaskLifecycle.start_task()` and `BeeTaskLifecycle.finalize_task()`.
4. Use `BeeTaskPlanner.plan_ready_nodes()` with an explicit clock and bounds.
5. Convert a returned `BeeNodeLaunchIntent` to safe run metadata with `build_bee_launch_metadata()`.
6. A later launcher must create a normal durable run using existing runtime/session paths.

The planner only claims dependency-ready nodes. It does not create runs, execute tools, call LLMs, or run commands.

## Console And Metrics

- `/console/bee` renders safe Bee task and node summaries derived from durable run metadata.
- Prometheus may use low-cardinality labels: `task_kind`, `task_status`, `task_profile`, `node_kind`, `node_status`, and `node_profile`.
- Prometheus must not label by `task_id`, `node_id`, `topic_id`, `run_id`, `session_id`, workspace IDs, file paths, commands, prompts, content, or secrets.

## Verification

Use the focused Bee runtime smoke:

```bash
uv run pytest tests/coding_agent/test_bee_runtime.py -v
```

Useful surrounding checks:

```bash
uv run pytest tests/ui/test_developer_console.py -v
uv run pytest tests/coding_agent/test_observability.py -v
uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v
git diff --check -- .
```
