# Bee Workspace Usage

Bee workspace support is a local, generic contract for `.bee` templates and sanitized `.bee` run artifacts. It does not execute commands and does not replace durable Bee stores.

## Template Layout

```text
.bee/templates/<template_id>/
  metadata.yaml or metadata.json
  SKILL.md
  features/*.feature
  commands.yaml optional
```

`metadata.yaml` and `metadata.json` are parsed through the existing Bee manifest validation. `SKILL.md` and `features/*.feature` are documentation and acceptance intent only.

`commands.yaml` may declare command intent metadata:

```yaml
commands:
  - name: smoke
    profile: validation
    policy: existing_command_policy
    category: validation
    validation_label: pytest_smoke
    metadata:
      owner: local
```

The loader never executes these command intents. Any future execution must create normal durable runs/actions and pass existing HITL, approval, command, workspace, path, validation, and action-safety gates.

## Run Artifacts

```text
.bee/runs/<task_id-or-slug>/
  task.json
  report.md
  evidence/
  memory_candidates.yaml optional
```

`task.json` is a sanitized mirror of durable state, not the source of truth. It may carry task, template, topic, run, action, validation, node, status, and path references. It must not carry raw prompt/content/message/result/secret/text/command output/stdout/stderr/env.

## Console And Metrics

The Developer Console Bee page can show:

- durable Bee task and node launch summaries
- workspace template summaries
- workspace run artifact summaries
- non-executing command intent summaries

Workspace-backed template/run/command sections are visible only in local no-token mode or with an admin token. User-scoped tokens see only durable run data already visible to that user.

Prometheus metrics may use low-cardinality labels such as `template_kind`, `template_profile`, `command_category`, `command_policy`, and `command_status`. They must not use `template_id`, `task_id`, `topic_id`, `run_id`, `session_id`, `node_id`, file paths, command strings, prompt, content, or secret labels.

## Local Smoke

Run the focused local dogfood smoke:

```bash
uv run pytest tests/coding_agent/test_bee_workspace.py -k local_dogfood_smoke -v
```

Run the phase-level verification:

```bash
uv run pytest tests/coding_agent/test_bee_workspace.py -v
uv run pytest tests/coding_agent/test_bee_runtime.py -v
uv run pytest tests/ui/test_developer_console.py -v
uv run pytest tests/coding_agent/test_observability.py tests/coding_agent/test_observability_platform_smoke.py -v
git diff --check -- .
```
