# Bee Command Bridge Implementation Report

## Scope

G110-G117 added a generic Coding Agent product-layer bridge from Bee command
intent references to existing local action safety and validation surfaces.

## Landed goals

- G110 mapped the current Bee runtime, workspace, action safety, validation,
  console, observability, and test surfaces.
- G111 accepted ADR-0043 for Bee command bridge boundaries.
- G112 added safe Bee node `command_ref` support while keeping executable
  fields rejected.
- G113 added non-executing command intent resolution from workspace templates.
- G114 added command policy and approval routing plans without execution.
- G115 routed validation Bee nodes through the existing `ValidationRunner`.
- G116 made Bee node completion evidence-backed.
- G117 added final console/metrics smoke coverage and this report.

## Safety outcomes

- `commands.yaml` remains declarative intent metadata and never grants
  execution rights.
- Command candidates are explicit caller input and are evaluated through the
  existing command policy.
- Denied and approval-required commands do not execute.
- Non-validation Bee nodes cannot execute through the validation bridge.
- Bee node completion requires accepted evidence or passed validation evidence.
- Safe summaries do not include raw command strings, command output, stdout,
  stderr, prompts, messages, secrets, or raw evidence references.
- Prometheus metrics use low-cardinality labels and omit task IDs, node IDs,
  command strings, and file paths.

## Verification

- `uv run pytest tests/coding_agent/test_bee_command_bridge.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_validation_runner.py -q`
- `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
- `uv run pytest tests/coding_agent/test_bee_workspace.py -q`
- `uv run pytest tests/ui/test_developer_console.py -q`
- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -q`
- `uv run ruff format --check src/coding_agent/bee_command_bridge.py tests/coding_agent/test_bee_command_bridge.py src/coding_agent/ui/developer_console.py`
- `uv run ruff check src/coding_agent/bee_command_bridge.py tests/coding_agent/test_bee_command_bridge.py src/coding_agent/ui/developer_console.py`
- `git diff --check -- .`

## Remaining risks

- This phase intentionally does not add external executors, Docker, Kubernetes,
  Argo, desktop, bridge app, or multi-agent execution.
- Completion decisions are product-layer decisions; callers must still persist
  durable Bee node status through existing Bee runtime APIs.
