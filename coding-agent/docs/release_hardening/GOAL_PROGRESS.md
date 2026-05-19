# Release Hardening And Contract Stabilization Goal Progress

Date started: 2026-05-20
Baseline: Durable Runtime G00-G11, Context System + Evaluation G12-G24, and Action Safety + Workspace Execution G25-G37 are complete on `main`.

This file is the phase ledger for G38-G45. Before each goal, append the goal id, intended files, verification commands, and stop criteria. After each goal, append changed files, tests run, results, and remaining risks.

## Phase Constraints

- Do not rewrite AgentKit Core.
- Do not change durable runtime semantics.
- Do not change context system semantics.
- Do not change action-safety semantics.
- Do not implement schedule, desktop, bridge, proactive agent, or full Docker sandbox behavior.
- Do not require real external LLM calls, production credentials, or external services.
- Prefer deterministic tests and local fixtures.
- If full-repository formatting causes large diffs, isolate them clearly and do not mix behavior changes.
- Do not add raw prompt, content, message, result, secret, or text values to trace/span attributes.

## Goal Map

No pre-existing repository document defined G38-G45 individually. The following map decomposes the requested phase into sequential, reviewable goals.

| Goal | Scope |
| --- | --- |
| G38 | Current-state audit, release-hardening goal map, and ledger setup. |
| G39 | Central release verification manifest for preserved regression gates. |
| G40 | Package/import contract smoke tests for `agentkit` and `coding_agent` boundaries. |
| G41 | CLI and entrypoint contract smoke tests that require no provider credentials. |
| G42 | Documentation command and boundary consistency checks. |
| G43 | Release safety contract checks for observability metadata and no raw sensitive trace attributes. |
| G44 | Packaging and JSONL compatibility release smoke checks. |
| G45 | Final implementation report, acceptance audit, PR cleanup verification, and baseline rerun. |

## G38 - Current-State Audit And Phase Ledger

Status: in progress.

### Before

Goal id: G38

Intended files:

- `docs/release_hardening/CURRENT_STATE.md`
- `docs/release_hardening/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/release-hardening-g38-current-state.md`

Verification commands:

- `test -f docs/release_hardening/CURRENT_STATE.md`
- `test -f docs/release_hardening/GOAL_PROGRESS.md`
- `test -f .opencode/prompts/tasks/release-hardening-g38-current-state.md`
- `uv run pytest tests/integration/test_durable_runtime_smoke.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/coding_agent/evaluation/ -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `git diff --check -- .`

Stop criteria:

- Cannot identify release-hardening scope without changing G00-G37 behavior.
- G38 would require external services, production credentials, or real LLM calls.
- Deterministic verification commands cannot be produced.
- More than two fix iterations fail for the same reason.

Postmortem routing:

- G38 is docs/task-packet scoped and does not directly match `postmortem/index.yaml` production `related_files`.
- Later goals touching CLI, runtime, storage, observability, or tool surfaces must consult matching postmortem entries before implementation or review.

### After

Changed files:

- `docs/release_hardening/CURRENT_STATE.md`
- `docs/release_hardening/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/release-hardening-g38-current-state.md`

Tests run:

- `test -f docs/release_hardening/CURRENT_STATE.md`
- `test -f docs/release_hardening/GOAL_PROGRESS.md`
- `test -f .opencode/prompts/tasks/release-hardening-g38-current-state.md`
- `uv run pytest tests/integration/test_durable_runtime_smoke.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/coding_agent/evaluation/ -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `git diff --check -- .`

Results:

- File existence checks passed.
- Durable runtime smoke tests passed: 6 passed, 32 dependency deprecation warnings from `slowapi`.
- Context-system smoke test passed: 1 passed.
- Action-safety smoke test passed: 1 passed.
- Evaluation tests passed: 20 passed.
- AgentKit build_context/runtime-stage span tests passed: 8 passed, 29 deselected.
- Diff whitespace check passed.

Remaining risks:

- G38 is documentation and scope setup only; it does not add new release gates yet.
- The G38-G45 goal map is inferred from the release-hardening objective because no prior repository document defined those goal ids.

## G39 - Release Verification Manifest

Status: in progress.

### Before

Goal id: G39

Intended files:

- `docs/release_hardening/release-verification.yaml`
- `src/coding_agent/verification/release_manifest.py`
- `src/coding_agent/verification/__init__.py`
- `tests/coding_agent/test_release_verification_manifest.py`
- `docs/release_hardening/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/release-hardening-g39-verification-manifest.md`

Verification commands:

- `uv run pytest tests/coding_agent/test_release_verification_manifest.py -v`
- `uv run pytest tests/coding_agent/test_verification.py tests/cli/test_verify.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/verification tests/coding_agent/test_release_verification_manifest.py`
- `uv run ruff check src/coding_agent/verification tests/coding_agent/test_release_verification_manifest.py`
- `git diff --check -- .`

Stop criteria:

- Manifest execution requires external services, production credentials, real LLM calls, or shell-specific syntax.
- Loader changes existing task-packet verification semantics from ADR-0007.
- Manifest format cannot be validated deterministically with local fixtures.
- More than two fix iterations fail for the same reason.

Postmortem routing:

- G39 touches `src/coding_agent/verification/`, new tests, and release docs. No direct `postmortem/index.yaml` `related_files` match was found for these paths.

### After

Changed files:

- `docs/release_hardening/release-verification.yaml`
- `src/coding_agent/verification/release_manifest.py`
- `src/coding_agent/verification/__init__.py`
- `tests/coding_agent/test_release_verification_manifest.py`
- `docs/release_hardening/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/release-hardening-g39-verification-manifest.md`

Tests run:

- `uv run pytest tests/coding_agent/test_release_verification_manifest.py -v`
- `uv run pytest tests/coding_agent/test_verification.py tests/cli/test_verify.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/verification tests/coding_agent/test_release_verification_manifest.py`
- `uv run ruff check src/coding_agent/verification tests/coding_agent/test_release_verification_manifest.py`
- `git diff --check -- .`

Results:

- Release verification manifest tests passed: 3 passed.
- Existing task-packet verification and CLI verify tests passed: 22 passed.
- Context-system smoke test passed: 1 passed.
- Action-safety smoke test passed: 1 passed.
- AgentKit build_context/runtime-stage span tests passed: 8 passed, 29 deselected.
- Scoped ruff format/check passed for `src/coding_agent/verification` and the release manifest test.
- Diff whitespace check passed.

Local review:

- The first implementation attempt exposed an incomplete negative-test fixture: malformed fixtures lacked `description`, so they failed before the intended duplicate-id and shell-syntax checks. G39 fixed the fixture setup, then reran the red/green target tests successfully.

Remaining risks:

- G39 adds a central manifest and deterministic loader, but it does not automatically execute release gates in CI.
- The manifest intentionally rejects shell syntax and relies on explicit single-process commands, matching the current ADR-0007 verification contract.

## G40 - Package Import Contract

Status: in progress.

### Before

Goal id: G40

Intended files:

- `tests/coding_agent/test_package_import_contract.py`
- `docs/release_hardening/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/release-hardening-g40-import-contract.md`

Verification commands:

- `uv run pytest tests/coding_agent/test_package_import_contract.py tests/coding_agent/test_bootstrap.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check tests/coding_agent/test_package_import_contract.py`
- `uv run ruff check tests/coding_agent/test_package_import_contract.py`
- `git diff --check -- .`

Stop criteria:

- Import-contract coverage requires changing AgentKit Core or package semantics beyond narrow test-backed corrections.
- Import smoke tests require provider credentials, real LLM calls, or external services.
- Tests would need to assert private implementation details unrelated to package/import boundaries.
- More than two fix iterations fail for the same reason.

Postmortem routing:

- G40 adds tests only and does not modify files listed in `postmortem/index.yaml`.

### After

Changed files:

- `tests/coding_agent/test_package_import_contract.py`
- `docs/release_hardening/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/release-hardening-g40-import-contract.md`

Tests run:

- `uv run pytest tests/coding_agent/test_package_import_contract.py tests/coding_agent/test_bootstrap.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check tests/coding_agent/test_package_import_contract.py`
- `uv run ruff check tests/coding_agent/test_package_import_contract.py`
- `git diff --check -- .`

Results:

- Package import contract and bootstrap tests passed: 25 passed.
- Context-system smoke test passed: 1 passed.
- Action-safety smoke test passed: 1 passed.
- AgentKit build_context/runtime-stage span tests passed: 8 passed, 29 deselected.
- Scoped ruff format/check passed for `tests/coding_agent/test_package_import_contract.py`.
- Diff whitespace check passed.

Local review:

- Local review found and G40 fixed three package-contract test issues: `coding_agent` lazy-import checks now run in an isolated subprocess, `agentkit` import checks now assert no `coding_agent` modules are loaded, and `src/agentkit` checks now parse AST imports instead of scanning raw text.

Remaining risks:

- G40 adds import/package contract smoke tests only; it does not prove wheel install behavior from a built artifact.
- The AST scan covers direct Python imports under `src/agentkit`; it does not inspect dynamic imports built from strings.

## G41 - CLI Entrypoint Contract

Status: in progress.

### Before

Goal id: G41

Intended files:

- `tests/cli/test_entrypoint_contract.py`
- `docs/release_hardening/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/release-hardening-g41-cli-entrypoints.md`

Verification commands:

- `uv run pytest tests/cli/test_entrypoint_contract.py tests/cli/test_commands.py tests/cli/test_verify.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check tests/cli/test_entrypoint_contract.py`
- `uv run ruff check tests/cli/test_entrypoint_contract.py`
- `git diff --check -- .`

Stop criteria:

- CLI smoke tests require real agent runs, provider API keys, external services, or server startup.
- Entrypoint contract checks require changing durable runtime, context-system, or action-safety semantics.
- Test coverage would need to assert unstable Click formatting beyond command names and required guidance.
- More than two fix iterations fail for the same reason.

Postmortem routing:

- G41 adds CLI tests and touches no production files. Existing CLI command tests are included because PM-0001 lists CLI command/input surfaces as recurring review-risk areas.

### After

Changed files:

- `tests/cli/test_entrypoint_contract.py`
- `docs/release_hardening/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/release-hardening-g41-cli-entrypoints.md`

Tests run:

- `uv run pytest tests/cli/test_entrypoint_contract.py tests/cli/test_commands.py tests/cli/test_verify.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check tests/cli/test_entrypoint_contract.py`
- `uv run ruff check tests/cli/test_entrypoint_contract.py`
- `git diff --check -- .`

Results:

- CLI entrypoint, slash-command, and verify CLI tests passed: 51 passed.
- Context-system smoke test passed: 1 passed.
- Action-safety smoke test passed: 1 passed.
- AgentKit build_context/runtime-stage span tests passed: 8 passed, 29 deselected.
- Scoped ruff format/check passed for `tests/cli/test_entrypoint_contract.py`.
- Diff whitespace check passed.

Remaining risks:

- G41 checks command help and non-interactive default behavior only; it does not execute real agent turns or start the HTTP server.
- The subprocess module help check validates installed source import behavior through `PYTHONPATH=src`, not a built wheel.

## G42 - Documentation Command And Boundary Consistency

Status: in progress.

### Before

Goal id: G42

Intended files:

- `tests/coding_agent/test_release_documentation_contract.py`
- `docs/release_hardening/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/release-hardening-g42-doc-contract.md`

Verification commands:

- `uv run pytest tests/coding_agent/test_release_documentation_contract.py -v`
- `uv run pytest tests/coding_agent/test_release_verification_manifest.py tests/cli/test_entrypoint_contract.py tests/coding_agent/test_package_import_contract.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check tests/coding_agent/test_release_documentation_contract.py`
- `uv run ruff check tests/coding_agent/test_release_documentation_contract.py`
- `git diff --check -- .`

Stop criteria:

- Documentation contract checks require changing CLI, runtime, context-system, or action-safety behavior.
- Checks require external services, production credentials, real LLM calls, or server startup.
- README parsing becomes broad enough to treat archived design docs as live release contracts.
- More than two fix iterations fail for the same reason.

Postmortem routing:

- G42 adds tests and release docs only. It does not modify production files listed in `postmortem/index.yaml`.

### After

Changed files:

- `tests/coding_agent/test_release_documentation_contract.py`
- `docs/release_hardening/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/release-hardening-g42-doc-contract.md`

Tests run:

- `uv run pytest tests/coding_agent/test_release_documentation_contract.py -v`
- `uv run pytest tests/coding_agent/test_release_verification_manifest.py tests/cli/test_entrypoint_contract.py tests/coding_agent/test_package_import_contract.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check tests/coding_agent/test_release_documentation_contract.py`
- `uv run ruff check tests/coding_agent/test_release_documentation_contract.py`
- `git diff --check -- .`

Results:

- Release documentation contract tests passed: 3 passed.
- Release manifest, CLI entrypoint, and package import contract tests passed: 10 passed.
- Context-system smoke test passed: 1 passed.
- Action-safety smoke test passed: 1 passed.
- AgentKit build_context/runtime-stage span tests passed: 8 passed, 29 deselected.
- Scoped ruff format/check passed for `tests/coding_agent/test_release_documentation_contract.py`.
- Diff whitespace check passed.

Local review:

- Initial test implementation was too strict about README tree-block spacing; G42 relaxed that assertion to match the documented boundary semantics rather than exact decoration.

Remaining risks:

- G42 treats `README.md` and `docs/release_hardening/release-verification.yaml` as live release contracts; archived design docs under `docs/superpowers/` are intentionally excluded.
- The docs command parser covers fenced `bash` command examples and does not attempt to lint prose-only command mentions.
