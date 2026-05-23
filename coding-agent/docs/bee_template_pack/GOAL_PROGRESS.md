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
