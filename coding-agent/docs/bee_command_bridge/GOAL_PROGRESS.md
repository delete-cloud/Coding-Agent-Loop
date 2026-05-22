# Bee Command Bridge Goal Progress

This ledger tracks G110-G117 for the Bee Command Intent Execution Bridge / Local Safe Executor phase.

## G110_BEE_COMMAND_BRIDGE_CURRENT_STATE_MAP

### Before

- Goal id: G110_BEE_COMMAND_BRIDGE_CURRENT_STATE_MAP
- Intended files:
  - `docs/bee_command_bridge/GOAL_PROGRESS.md`
  - `docs/bee_command_bridge/CURRENT_STATE.md`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -q`
  - `git diff --check -- .`
- Stop criteria:
  - Current state map exists.
  - It documents Bee runtime/workspace command intent behavior, action safety, validation runner, workspace provider, HITL, console, observability, tests, and likely files to modify.
  - No production code changes are made.

### After

- Changed files:
  - `docs/bee_command_bridge/GOAL_PROGRESS.md`
  - `docs/bee_command_bridge/CURRENT_STATE.md`
- Tests run:
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -q`
  - `git diff --check -- .`
- Results:
  - Bee runtime baseline passed.
  - Bee workspace smoke passed.
  - Whitespace diff check passed.
  - Production code unchanged.
- Remaining risks:
  - G111 still needs ADR-backed command bridge boundaries.
  - No command intent execution bridge exists yet.

## G111_BEE_COMMAND_BRIDGE_ADR

### Before

- Goal id: G111_BEE_COMMAND_BRIDGE_ADR
- Intended files:
  - `docs/bee_command_bridge/GOAL_PROGRESS.md`
  - `docs/adr/0043-bee-command-bridge.md`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -q`
  - `git diff --check -- .`
- Stop criteria:
  - ADR exists and is accepted.
  - It defines command intent bridge scope, no-leak rules, safety gates, evidence-based completion, validation behavior, observability/console boundaries, and non-goals.
  - No production code changes are made.

### After

- Changed files:
  - `docs/bee_command_bridge/GOAL_PROGRESS.md`
  - `docs/adr/0043-bee-command-bridge.md`
- Tests run:
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -q`
  - `git diff --check -- .`
- Results:
  - Bee runtime baseline passed.
  - Bee workspace smoke passed.
  - Whitespace diff check passed.
  - Production code unchanged.
- Remaining risks:
  - G112 still needs `command_ref` manifest support.
  - Bridge implementation, validation execution, evidence completion, console, metrics, and final smoke remain pending.

## G112_BEE_NODE_COMMAND_REF

### Before

- Goal id: G112_BEE_NODE_COMMAND_REF
- Intended files:
  - `docs/bee_command_bridge/GOAL_PROGRESS.md`
  - `docs/adr/0043-bee-command-bridge.md`
  - `src/coding_agent/bee_runtime.py`
  - `tests/coding_agent/test_bee_runtime.py`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -v`
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -q`
  - `uv run ruff format --check src/coding_agent/bee_runtime.py tests/coding_agent/test_bee_runtime.py`
  - `uv run ruff check src/coding_agent/bee_runtime.py tests/coding_agent/test_bee_runtime.py`
  - `git diff --check -- .`
- Stop criteria:
  - `BeeNodeManifest` supports safe `command_ref`.
  - `command_ref` is parsed, validated, and propagated as safe launch metadata.
  - Raw executable fields such as `command` and `commands` remain rejected.
  - No command execution bridge is introduced in this goal.

### After

- Changed files:
  - `docs/bee_command_bridge/GOAL_PROGRESS.md`
  - `docs/adr/0043-bee-command-bridge.md`
  - `src/coding_agent/bee_runtime.py`
  - `tests/coding_agent/test_bee_runtime.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -v`
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -q`
  - `uv run ruff format --check src/coding_agent/bee_runtime.py tests/coding_agent/test_bee_runtime.py`
  - `uv run ruff check src/coding_agent/bee_runtime.py tests/coding_agent/test_bee_runtime.py`
  - `git diff --check -- .`
- Results:
  - `command_ref` parses as a safe Bee node reference.
  - `command_ref` appears in launch metadata as workspace-intent-only reference data.
  - Raw executable command fields remain rejected.
  - Bee runtime and Bee workspace tests passed.
  - Scoped formatting, lint, and whitespace checks passed.
- Remaining risks:
  - G113 still needs the non-executing bridge resolver.
  - No command intent execution occurs yet.

## G113_BEE_COMMAND_INTENT_RESOLVER

### Before

- Goal id: G113_BEE_COMMAND_INTENT_RESOLVER
- Intended files:
  - `docs/bee_command_bridge/GOAL_PROGRESS.md`
  - `docs/adr/0043-bee-command-bridge.md`
  - `src/coding_agent/bee_command_bridge.py`
  - `tests/coding_agent/test_bee_command_bridge.py`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_bee_command_bridge.py -v`
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -q`
  - `uv run ruff format --check src/coding_agent/bee_command_bridge.py tests/coding_agent/test_bee_command_bridge.py`
  - `uv run ruff check src/coding_agent/bee_command_bridge.py tests/coding_agent/test_bee_command_bridge.py`
  - `git diff --check -- .`
- Stop criteria:
  - Bridge resolver can resolve a Bee node `command_ref` to a declared workspace command intent.
  - Missing, unknown, or disabled command refs fail closed.
  - Resolver does not execute commands or grant permissions.
  - Existing Bee runtime and workspace behavior remains compatible.

### After

- Changed files:
  - `docs/bee_command_bridge/GOAL_PROGRESS.md`
  - `docs/adr/0043-bee-command-bridge.md`
  - `src/coding_agent/bee_command_bridge.py`
  - `tests/coding_agent/test_bee_command_bridge.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_bee_command_bridge.py -v`
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -q`
  - `uv run ruff format --check src/coding_agent/bee_command_bridge.py tests/coding_agent/test_bee_command_bridge.py`
  - `uv run ruff check src/coding_agent/bee_command_bridge.py tests/coding_agent/test_bee_command_bridge.py`
  - `git diff --check -- .`
- Results:
  - Command bridge resolver resolves declared workspace command intents without execution.
  - Missing, unknown, and disabled intents fail closed with explicit statuses.
  - Bee runtime and Bee workspace regression tests passed.
  - Scoped formatting, lint, and whitespace checks passed after formatting one new test file.
- Remaining risks:
  - G114 still needs command policy denial/approval-required bridge decisions.
  - No command execution, validation execution, evidence completion, console, or metrics integration occurs yet.

## G114_BEE_COMMAND_POLICY_DECISION

### Before

- Goal id: G114_BEE_COMMAND_POLICY_DECISION
- Intended files:
  - `docs/bee_command_bridge/GOAL_PROGRESS.md`
  - `docs/adr/0043-bee-command-bridge.md`
  - `src/coding_agent/bee_command_bridge.py`
  - `tests/coding_agent/test_bee_command_bridge.py`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_bee_command_bridge.py -v`
  - `uv run pytest tests/coding_agent/action_safety/test_command_policy.py -q`
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -q`
  - `uv run ruff format --check src/coding_agent/bee_command_bridge.py tests/coding_agent/test_bee_command_bridge.py`
  - `uv run ruff check src/coding_agent/bee_command_bridge.py tests/coding_agent/test_bee_command_bridge.py`
  - `git diff --check -- .`
- Stop criteria:
  - Bridge evaluates an explicit command candidate through existing command policy.
  - Policy denied intents return a denied bridge plan without execution.
  - Approval-required intents return an approval-required bridge plan without execution.
  - Bridge safe summaries omit raw command strings and command output.

### After

- Changed files:
  - `docs/bee_command_bridge/GOAL_PROGRESS.md`
  - `docs/adr/0043-bee-command-bridge.md`
  - `src/coding_agent/bee_command_bridge.py`
  - `tests/coding_agent/test_bee_command_bridge.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_bee_command_bridge.py -v`
  - `uv run pytest tests/coding_agent/action_safety/test_command_policy.py -q`
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -q`
  - `uv run ruff format --check src/coding_agent/bee_command_bridge.py tests/coding_agent/test_bee_command_bridge.py`
  - `uv run ruff check src/coding_agent/bee_command_bridge.py tests/coding_agent/test_bee_command_bridge.py`
  - `git diff --check -- .`
- Results:
  - Bridge plans now evaluate explicit command candidates through existing command policy and approval routing.
  - Policy-denied and approval-required outcomes return non-executing bridge plans.
  - Safe summaries omit raw command strings and command output.
  - Command policy, Bee runtime, and Bee workspace regression tests passed.
  - Scoped formatting, lint, and whitespace checks passed.
- Remaining risks:
  - G115 still needs validation-node execution through `ValidationRunner`.
  - Ready bridge plans still do not execute commands or complete Bee nodes.
