# Bee Workspace Goal Progress

This ledger tracks G102-G109 for the Bee Workspace Contract / Local Template Dogfood phase.

## G102_BEE_WORKSPACE_CURRENT_STATE_MAP

### Before

- Goal id: G102_BEE_WORKSPACE_CURRENT_STATE_MAP
- Intended files:
  - `docs/bee_workspace/GOAL_PROGRESS.md`
  - `docs/bee_workspace/CURRENT_STATE.md`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
  - `git diff --check -- .`
- Stop criteria:
  - No production code changes are made.
  - Current Bee runtime, workspace provider, console, observability, and test surfaces are mapped.
  - Exact files/functions to modify in later goals are identified.

### After

- Changed files:
  - `docs/bee_workspace/GOAL_PROGRESS.md`
  - `docs/bee_workspace/CURRENT_STATE.md`
- Tests run:
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
  - `git diff --check -- .`
- Results:
  - Bee runtime baseline passed.
  - Whitespace diff check passed.
  - Production code unchanged.
- Remaining risks:
  - G103 must lock the workspace file contract before adding parser/writer code.
  - G104-G109 should keep workspace-local artifacts generic and avoid command execution.

## G103_BEE_WORKSPACE_ADR

### Before

- Goal id: G103_BEE_WORKSPACE_ADR
- Intended files:
  - `docs/bee_workspace/GOAL_PROGRESS.md`
  - `docs/adr/0042-bee-workspace-contract.md`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
  - `git diff --check -- .`
- Stop criteria:
  - No production code changes are made.
  - ADR defines `.bee/templates`, `.bee/runs`, `task.json`, report/evidence, memory candidate, and `commands.yaml` boundaries.
  - ADR preserves existing Bee runtime, workspace provider, action safety, observability, and console contracts.

### After

- Changed files:
  - `docs/bee_workspace/GOAL_PROGRESS.md`
  - `docs/adr/0042-bee-workspace-contract.md`
- Tests run:
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
  - `git diff --check -- .`
- Results:
  - Bee runtime baseline passed.
  - Whitespace diff check passed.
  - Production code unchanged.
- Remaining risks:
  - Acceptance criteria remain unchecked until G104-G109 implement and verify the contract.
  - `commands.yaml` support must remain non-executing until routed through existing safety gates.

## G104_BEE_WORKSPACE_TEMPLATE_DISCOVERY

### Before

- Goal id: G104_BEE_WORKSPACE_TEMPLATE_DISCOVERY
- Intended files:
  - `docs/bee_workspace/GOAL_PROGRESS.md`
  - `docs/adr/0042-bee-workspace-contract.md`
  - `src/coding_agent/bee_workspace.py`
  - `tests/coding_agent/test_bee_workspace.py`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -v`
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
  - `uv run ruff format --check src/coding_agent/bee_workspace.py tests/coding_agent/test_bee_workspace.py`
  - `uv run ruff check src/coding_agent/bee_workspace.py tests/coding_agent/test_bee_workspace.py`
  - `git diff --check -- .`
- Stop criteria:
  - `.bee/templates/<template_id>/metadata.yaml|json` can be discovered and parsed from a local workspace.
  - `SKILL.md`, `features/*.feature`, and optional `commands.yaml` paths are recognized without reading or executing command content.
  - Sensitive/executable metadata fields are rejected through existing Bee manifest safety validation.
  - No manifest builder, run artifact writer, command executor, external service, or AgentKit Core change is introduced.

### After

- Changed files:
  - `docs/bee_workspace/GOAL_PROGRESS.md`
  - `docs/adr/0042-bee-workspace-contract.md`
  - `src/coding_agent/bee_workspace.py`
  - `tests/coding_agent/test_bee_workspace.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -v`
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
  - `uv run ruff format --check src/coding_agent/bee_workspace.py tests/coding_agent/test_bee_workspace.py`
  - `uv run ruff check src/coding_agent/bee_workspace.py tests/coding_agent/test_bee_workspace.py`
  - `git diff --check -- .`
- Results:
  - Workspace template discovery and metadata parser tests passed.
  - Bee runtime baseline passed.
  - Scoped formatting, lint, and whitespace checks passed.
- Remaining risks:
  - G105 still needs the explicit template-to-manifest builder API.
  - `commands.yaml` is only detected as a path in G104; its safe non-executing contract parser remains G107.

## G105_BEE_WORKSPACE_MANIFEST_BUILDER

### Before

- Goal id: G105_BEE_WORKSPACE_MANIFEST_BUILDER
- Intended files:
  - `docs/bee_workspace/GOAL_PROGRESS.md`
  - `docs/adr/0042-bee-workspace-contract.md`
  - `src/coding_agent/bee_workspace.py`
  - `tests/coding_agent/test_bee_workspace.py`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -v`
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
  - `uv run ruff format --check src/coding_agent/bee_workspace.py tests/coding_agent/test_bee_workspace.py`
  - `uv run ruff check src/coding_agent/bee_workspace.py tests/coding_agent/test_bee_workspace.py`
  - `git diff --check -- .`
- Stop criteria:
  - Workspace templates can be converted to the existing `BeeTaskManifest` object without introducing a second manifest model.
  - `template_id` is recorded only as safe manifest metadata.
  - Template-to-manifest conversion preserves existing Bee parser validation and no-leak rules.
  - No run artifact writer, command parser/executor, external service, or AgentKit Core change is introduced.

### After

- Changed files:
  - `docs/bee_workspace/GOAL_PROGRESS.md`
  - `docs/adr/0042-bee-workspace-contract.md`
  - `src/coding_agent/bee_workspace.py`
  - `tests/coding_agent/test_bee_workspace.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -v`
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
  - `uv run ruff format --check src/coding_agent/bee_workspace.py tests/coding_agent/test_bee_workspace.py`
  - `uv run ruff check src/coding_agent/bee_workspace.py tests/coding_agent/test_bee_workspace.py`
  - `git diff --check -- .`
- Results:
  - Workspace template-to-manifest builder tests passed.
  - Bee runtime baseline passed.
  - Scoped formatting, lint, and whitespace checks passed.
- Remaining risks:
  - G106 still needs safe `.bee/runs/<task>/task.json` and report artifact writing.
  - `commands.yaml` remains path-only until the G107 non-executing contract parser.
