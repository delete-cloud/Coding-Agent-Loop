# Bee Template Pack / Domain Pack Integration Goal Progress

This ledger tracks G145-G153 execution. Each goal records intended files,
verification commands, stop criteria, changed files, test results, and
remaining risks before continuing to the next goal.

## G145_BEE_TEMPLATE_PACK_CURRENT_STATE_MAP

### Before

- Goal id: `G145_BEE_TEMPLATE_PACK_CURRENT_STATE_MAP`
- Intended files:
  - `docs/bee_template_pack/GOAL_PROGRESS.md`
  - `docs/bee_template_pack/CURRENT_STATE.md`
- Verification commands:
  - `rg -n "BeeTemplatePack|BeePackManifest|commands.yaml|BeeLaunch|memory candidate|TopicRangeIndex|Developer Console|observability|Out of scope" docs/bee_template_pack/CURRENT_STATE.md`
  - `uv run ruff format --check --preview docs/bee_template_pack/GOAL_PROGRESS.md docs/bee_template_pack/CURRENT_STATE.md`
  - `git diff --check -- .`
- Stop criteria:
  - Current-state map exists and covers template discovery, BeeTemplate
    validation, command intent parsing, Bee launch plans, memory/recall,
    console, observability, exact later modification points, tests to preserve,
    and explicit out-of-scope work.
  - No production code changes are made.

### After

- Changed files:
  - `docs/bee_template_pack/GOAL_PROGRESS.md`
  - `docs/bee_template_pack/CURRENT_STATE.md`
- Tests run:
  - `rg -n "BeeTemplatePack|BeePackManifest|commands.yaml|BeeLaunch|memory candidate|TopicRangeIndex|Developer Console|observability|Out of scope" docs/bee_template_pack/CURRENT_STATE.md`
  - `uv run ruff format --check --preview docs/bee_template_pack/GOAL_PROGRESS.md docs/bee_template_pack/CURRENT_STATE.md`
  - `git diff --check -- .`
- Results:
  - Current-state keyword coverage check passed.
  - Ruff format check reported both docs already formatted.
  - Git whitespace check passed.
- Remaining risks:
  - G146 still needs an ADR to lock the pack boundary before production code
    changes.

## G146_BEE_TEMPLATE_PACK_ADR

### Before

- Goal id: `G146_BEE_TEMPLATE_PACK_ADR`
- Intended files:
  - `docs/bee_template_pack/GOAL_PROGRESS.md`
  - `docs/adr/0047-bee-template-pack-domain-pack-integration.md`
- Verification commands:
  - `rg -n "BeeTemplatePack|BeePackManifest|BeeTemplatePackSource|BeePackRegistry|BeePackCompatibilityReport|BeePackDryRunPlan|DomainProfile|commands.yaml|nmem|Argo CD|Acceptance Criteria" docs/adr/0047-bee-template-pack-domain-pack-integration.md`
  - `uv run ruff format --check --preview docs/bee_template_pack/GOAL_PROGRESS.md docs/adr/0047-bee-template-pack-domain-pack-integration.md`
  - `git diff --check -- .`
- Stop criteria:
  - ADR exists and defines the generic Bee template pack/domain pack boundary.
  - ADR states template packs are data/config, validation never executes
    commands, `commands.yaml` cannot grant execution permission, pack memory
    candidates remain candidates until review, and nmem/Argo CD integration is
    deferred.
  - No production code changes are made.

### After

- Changed files:
  - `docs/bee_template_pack/GOAL_PROGRESS.md`
  - `docs/adr/0047-bee-template-pack-domain-pack-integration.md`
- Tests run:
  - `rg -n "BeeTemplatePack|BeePackManifest|BeeTemplatePackSource|BeePackRegistry|BeePackCompatibilityReport|BeePackDryRunPlan|DomainProfile|commands.yaml|nmem|Argo CD|Acceptance Criteria" docs/adr/0047-bee-template-pack-domain-pack-integration.md`
  - `uv run ruff format --check --preview docs/bee_template_pack/GOAL_PROGRESS.md docs/adr/0047-bee-template-pack-domain-pack-integration.md`
  - `git diff --check -- .`
- Results:
  - ADR keyword coverage check passed.
  - Ruff format check reported both docs already formatted.
  - Git whitespace check passed.
- Remaining risks:
  - G147 still needs the production manifest schema/loader and tests.

## G147_BEE_PACK_MANIFEST_SCHEMA_AND_LOADER

### Before

- Goal id: `G147_BEE_PACK_MANIFEST_SCHEMA_AND_LOADER`
- Intended files:
  - `docs/bee_template_pack/GOAL_PROGRESS.md`
  - `src/coding_agent/bee_template_pack.py`
  - `tests/coding_agent/test_bee_template_pack.py`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_bee_template_pack.py -v`
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -v`
  - `uv run ruff format --check --preview src/coding_agent/bee_template_pack.py tests/coding_agent/test_bee_template_pack.py docs/bee_template_pack/GOAL_PROGRESS.md`
  - `git diff --check -- .`
- Stop criteria:
  - Pack manifests load from `bee-pack.yaml`, `bee-pack.json`,
    `.bee/pack.yaml`, or `.bee/pack.json`.
  - `pack_id`, `name`, `version`, and referenced templates are validated.
  - Template ids are unique.
  - Missing manifest with safe `.bee/templates` becomes an implicit local pack.
  - Loader does not execute commands or grant execution permission.
  - Existing Bee workspace behavior remains unchanged.

### After

- Changed files:
  - `docs/bee_template_pack/GOAL_PROGRESS.md`
  - `src/coding_agent/bee_template_pack.py`
  - `tests/coding_agent/test_bee_template_pack.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_bee_template_pack.py -v`
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -v`
  - `uv run ruff format --check --preview src/coding_agent/bee_template_pack.py tests/coding_agent/test_bee_template_pack.py docs/bee_template_pack/GOAL_PROGRESS.md`
  - `uv run ruff check --preview src/coding_agent/bee_template_pack.py tests/coding_agent/test_bee_template_pack.py`
  - `git diff --check -- .`
- Results:
  - Focused pack manifest tests passed: 13 passed.
  - Existing Bee workspace regression passed: 54 passed.
  - Ruff format check, ruff check, and git whitespace check passed.
  - Initial red test failed on missing `coding_agent.bee_template_pack`; first
    fix iteration corrected an over-broad `description` key false positive.
- Remaining risks:
  - G148 still needs registry/discovery across multiple pack roots and explicit
    provenance APIs.

## G148_BEE_PACK_REGISTRY_AND_DISCOVERY

### Before

- Goal id: `G148_BEE_PACK_REGISTRY_AND_DISCOVERY`
- Intended files:
  - `docs/bee_template_pack/GOAL_PROGRESS.md`
  - `src/coding_agent/bee_template_pack.py`
  - `tests/coding_agent/test_bee_template_pack.py`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_bee_template_pack.py -v`
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -v`
  - `uv run ruff format --check --preview src/coding_agent/bee_template_pack.py tests/coding_agent/test_bee_template_pack.py docs/bee_template_pack/GOAL_PROGRESS.md`
  - `uv run ruff check --preview src/coding_agent/bee_template_pack.py tests/coding_agent/test_bee_template_pack.py`
  - `git diff --check -- .`
- Stop criteria:
  - `BeePackRegistry` can discover one or more local/fixture/imported pack
    roots.
  - Registry lists packs and templates by pack.
  - Registry loads templates through pack context.
  - Unknown packs/templates are rejected.
  - Pack/template provenance is available without executing commands.

### After

- Changed files:
  - `docs/bee_template_pack/GOAL_PROGRESS.md`
  - `src/coding_agent/bee_template_pack.py`
  - `tests/coding_agent/test_bee_template_pack.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_bee_template_pack.py -v`
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -v`
  - `uv run ruff format --check --preview src/coding_agent/bee_template_pack.py tests/coding_agent/test_bee_template_pack.py docs/bee_template_pack/GOAL_PROGRESS.md`
  - `uv run ruff check --preview src/coding_agent/bee_template_pack.py tests/coding_agent/test_bee_template_pack.py`
  - `git diff --check -- .`
- Results:
  - Focused pack manifest/registry tests passed: 19 passed.
  - Existing Bee workspace regression passed: 54 passed.
  - Ruff format check, ruff check, and git whitespace check passed.
  - Initial red test failed on missing `BeePackRegistry`; implementation added
    registry summaries, template lookup, and provenance records.
- Remaining risks:
  - G149 still needs compatibility reports, static command-ref validation, and
    unsupported executor/no-raw-artifact findings.

## G149_BEE_PACK_COMPATIBILITY_VALIDATOR

### Before

- Goal id: `G149_BEE_PACK_COMPATIBILITY_VALIDATOR`
- Intended files:
  - `docs/bee_template_pack/GOAL_PROGRESS.md`
  - `src/coding_agent/bee_template_pack.py`
  - `tests/coding_agent/test_bee_template_pack.py`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_bee_template_pack.py -v`
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -v`
  - `uv run ruff format --check --preview src/coding_agent/bee_template_pack.py tests/coding_agent/test_bee_template_pack.py docs/bee_template_pack/GOAL_PROGRESS.md`
  - `uv run ruff check --preview src/coding_agent/bee_template_pack.py tests/coding_agent/test_bee_template_pack.py`
  - `git diff --check -- .`
- Stop criteria:
  - Static compatibility validation reports compatible, warning, or
    incompatible status.
  - Validator checks pack/template loading, SKILL/features, commands.yaml
    intents, command_ref references, node dependencies, risk/report contracts,
    optional memory candidate contract, executor capability declarations, and
    forbidden raw static keys.
  - Report serialization is sanitized and deterministic.
  - Validator does not execute commands.

### After

- Changed files:
  - `docs/bee_template_pack/GOAL_PROGRESS.md`
  - `src/coding_agent/bee_template_pack.py`
  - `tests/coding_agent/test_bee_template_pack.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_bee_template_pack.py -v`
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -v`
  - `uv run ruff format --check --preview src/coding_agent/bee_template_pack.py tests/coding_agent/test_bee_template_pack.py docs/bee_template_pack/GOAL_PROGRESS.md`
  - `uv run ruff check --preview src/coding_agent/bee_template_pack.py tests/coding_agent/test_bee_template_pack.py`
  - `git diff --check -- .`
- Results:
  - Focused pack manifest/registry/compatibility tests passed: 27 passed.
  - Existing Bee workspace regression passed: 54 passed.
  - Ruff format check, ruff check, and git whitespace check passed.
  - Initial red test failed on missing `validate_bee_pack_compatibility`;
    implementation added sanitized report/check/finding/template summaries and
    static validator logic.
- Remaining risks:
  - G150 still needs dry-run launch planning and explicit no-durable-task
    checks.

## G150_BEE_PACK_DRY_RUN_LAUNCH_PLAN

### Before

- Goal id: `G150_BEE_PACK_DRY_RUN_LAUNCH_PLAN`
- Intended files:
  - `docs/bee_template_pack/GOAL_PROGRESS.md`
  - `src/coding_agent/bee_template_pack.py`
  - `tests/coding_agent/test_bee_template_pack.py`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_bee_template_pack.py -v`
  - `uv run pytest tests/coding_agent/test_bee_launch.py -v`
  - `uv run ruff format --check --preview src/coding_agent/bee_template_pack.py tests/coding_agent/test_bee_template_pack.py docs/bee_template_pack/GOAL_PROGRESS.md`
  - `uv run ruff check --preview src/coding_agent/bee_template_pack.py tests/coding_agent/test_bee_template_pack.py`
  - `git diff --check -- .`
- Stop criteria:
  - Pack dry-run planning validates inputs, topic/workspace policy, command
    intent resolution, and executor capability declarations.
  - Dry-run previews launch/topic/task/nodes/command intents and expected
    task.json/report/evidence/memory paths.
  - Dry-run creates no durable task and executes no commands.

### After

- Changed files:
  - `docs/bee_template_pack/GOAL_PROGRESS.md`
  - `src/coding_agent/bee_template_pack.py`
  - `tests/coding_agent/test_bee_template_pack.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_bee_template_pack.py -v`
  - `uv run pytest tests/coding_agent/test_bee_launch.py -v`
  - `uv run ruff format --check --preview src/coding_agent/bee_template_pack.py tests/coding_agent/test_bee_template_pack.py docs/bee_template_pack/GOAL_PROGRESS.md`
  - `uv run ruff check --preview src/coding_agent/bee_template_pack.py tests/coding_agent/test_bee_template_pack.py`
  - `git diff --check -- .`
- Results:
  - Focused pack manifest/registry/compatibility/dry-run tests passed:
    32 passed.
  - Existing Bee launch regression passed: 43 passed.
  - Ruff format check, ruff check, and git whitespace check passed.
  - Initial red test failed on missing `build_bee_pack_dry_run_plan`;
    implementation added deterministic non-durable dry-run previews.
- Remaining risks:
  - G151 still needs memory candidate, topic range, and recall binding for
    pack/template/domain provenance.
