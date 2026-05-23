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

## G151_BEE_PACK_MEMORY_AND_RECALL_BINDING

### Before

- Goal id: `G151_BEE_PACK_MEMORY_AND_RECALL_BINDING`
- Intended files:
  - `docs/bee_template_pack/GOAL_PROGRESS.md`
  - `src/coding_agent/topic_memory.py`
  - `src/coding_agent/topic_range_index.py`
  - `src/coding_agent/recall_context.py`
  - `tests/coding_agent/test_topic_memory.py`
  - `tests/coding_agent/test_topic_range_index.py`
  - `tests/coding_agent/test_recall_context.py`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_topic_memory.py tests/coding_agent/test_topic_range_index.py tests/coding_agent/test_recall_context.py -v`
  - `uv run pytest tests/coding_agent/test_cross_topic_memory_smoke.py -v`
  - `uv run ruff format --check --preview src/coding_agent/topic_memory.py src/coding_agent/topic_range_index.py src/coding_agent/recall_context.py tests/coding_agent/test_topic_memory.py tests/coding_agent/test_topic_range_index.py tests/coding_agent/test_recall_context.py docs/bee_template_pack/GOAL_PROGRESS.md`
  - `uv run ruff check --preview src/coding_agent/topic_memory.py src/coding_agent/topic_range_index.py src/coding_agent/recall_context.py tests/coding_agent/test_topic_memory.py tests/coding_agent/test_topic_range_index.py tests/coding_agent/test_recall_context.py`
  - `git diff --check -- .`
- Stop criteria:
  - Memory candidates can include safe pack/template/domain provenance.
  - Topic range documents/results can include pack/template/domain metadata.
  - Recall can filter/boost by domain profile and pack tags.
  - Accepted memory remains reference-only.
  - No raw artifact content is exposed.

### After

- Changed files:
  - `docs/bee_template_pack/GOAL_PROGRESS.md`
  - `src/coding_agent/topic_memory.py`
  - `src/coding_agent/topic_range_index.py`
  - `src/coding_agent/recall_context.py`
  - `tests/coding_agent/test_topic_memory.py`
  - `tests/coding_agent/test_topic_range_index.py`
  - `tests/coding_agent/test_recall_context.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_topic_memory.py tests/coding_agent/test_topic_range_index.py tests/coding_agent/test_recall_context.py -v`
  - `uv run pytest tests/coding_agent/test_cross_topic_memory_smoke.py -v`
  - `uv run ruff format --check --preview src/coding_agent/topic_memory.py src/coding_agent/topic_range_index.py src/coding_agent/recall_context.py tests/coding_agent/test_topic_memory.py tests/coding_agent/test_topic_range_index.py tests/coding_agent/test_recall_context.py docs/bee_template_pack/GOAL_PROGRESS.md`
  - `uv run ruff check --preview src/coding_agent/topic_memory.py src/coding_agent/topic_range_index.py src/coding_agent/recall_context.py tests/coding_agent/test_topic_memory.py tests/coding_agent/test_topic_range_index.py tests/coding_agent/test_recall_context.py`
  - `git diff --check -- .`
- Results:
  - Focused topic memory/range/recall tests passed: 29 passed.
  - Existing cross-topic memory smoke passed: 1 passed.
  - Ruff format check, ruff check, and git whitespace check passed.
  - Initial red tests failed on missing pack/domain fields in memory,
    topic-range, and recall APIs; implementation added safe provenance fields,
    filters, and accepted-memory ranking boosts.
- Remaining risks:
  - G152 still needs console and low-cardinality observability surfaces for
    pack validation, template status, and dry-run plans.

## G152_BEE_PACK_CONSOLE_AND_OBSERVABILITY

### Before

- Goal id: `G152_BEE_PACK_CONSOLE_AND_OBSERVABILITY`
- Intended files:
  - `docs/bee_template_pack/GOAL_PROGRESS.md`
  - `src/coding_agent/ui/developer_console.py`
  - `src/coding_agent/ui/http_server.py`
  - `src/coding_agent/observability.py`
  - `tests/ui/test_developer_console.py`
  - `tests/coding_agent/test_observability.py`
- Verification commands:
  - `uv run pytest tests/ui/test_developer_console.py -v`
  - `uv run pytest tests/coding_agent/test_observability.py -v`
  - `uv run pytest tests/coding_agent/test_bee_template_pack.py -v`
  - `uv run ruff format --check --preview src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py src/coding_agent/observability.py tests/ui/test_developer_console.py tests/coding_agent/test_observability.py docs/bee_template_pack/GOAL_PROGRESS.md`
  - `uv run ruff check --preview src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py src/coding_agent/observability.py tests/ui/test_developer_console.py tests/coding_agent/test_observability.py`
  - `git diff --check -- .`
- Stop criteria:
  - Console renders pack list, pack templates, compatibility reports, and dry-run previews.
  - Console output excludes raw prompt/content/message/result/secret/text/command_output/stdout/stderr/env fields.
  - Pack observability records low-cardinality metrics only.
  - Existing Developer Console, observability, and Bee template pack tests pass.

### After

- Changed files:
  - `docs/bee_template_pack/GOAL_PROGRESS.md`
  - `src/coding_agent/ui/developer_console.py`
  - `src/coding_agent/ui/http_server.py`
  - `src/coding_agent/observability.py`
  - `tests/ui/test_developer_console.py`
  - `tests/coding_agent/test_observability.py`
- Tests run:
  - `uv run pytest tests/ui/test_developer_console.py -v`
  - `uv run pytest tests/coding_agent/test_observability.py -v`
  - `uv run pytest tests/coding_agent/test_bee_template_pack.py -v`
  - `uv run ruff format --check --preview src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py src/coding_agent/observability.py tests/ui/test_developer_console.py tests/coding_agent/test_observability.py docs/bee_template_pack/GOAL_PROGRESS.md`
  - `uv run ruff check --preview tests/ui/test_developer_console.py tests/coding_agent/test_observability.py`
  - `uv run ruff check --preview --select I src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py src/coding_agent/observability.py`
  - `git diff --check -- .`
- Results:
  - Developer Console tests passed: 45 passed.
  - Observability tests passed: 38 passed.
  - Bee template pack tests passed: 32 passed.
  - Ruff format check, test-file ruff check, production import-order ruff
    check, and git whitespace check passed.
  - Initial red tests failed on missing console pack sections and missing Bee
    pack metrics recorder methods; implementation added read-only pack
    summaries, compatibility/dry-run previews, and low-cardinality counters.
- Remaining risks:
  - Full `ruff check --preview` on the touched production modules still reports
    pre-existing broad lint debt in `observability.py` and `http_server.py`
    unrelated to this goal. G153 should keep verification scoped unless that
    debt is intentionally scheduled.
  - G153 still needs final end-to-end smoke tests and user-facing pack docs.

## G153_BEE_TEMPLATE_PACK_E2E_SMOKE_AND_DOCS

### Before

- Goal id: `G153_BEE_TEMPLATE_PACK_E2E_SMOKE_AND_DOCS`
- Intended files:
  - `docs/bee_template_pack/GOAL_PROGRESS.md`
  - `docs/bee_template_pack/USAGE.md`
  - `docs/bee_template_pack/IMPLEMENTATION_REPORT.md`
  - `tests/coding_agent/test_bee_template_pack_smoke.py`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_bee_template_pack_smoke.py -v`
  - `uv run pytest tests/coding_agent/test_cross_topic_memory_smoke.py -v`
  - `uv run pytest tests/coding_agent/test_external_executor.py -v`
  - `uv run pytest tests/coding_agent/test_external_executor_smoke.py -v`
  - `uv run pytest tests/coding_agent/test_bee_launch.py -v`
  - `uv run pytest tests/coding_agent/test_bee_command_bridge.py -v`
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -v`
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -v`
  - `uv run pytest tests/coding_agent/test_scheduled_runs.py -v`
  - `uv run pytest tests/coding_agent/test_topic_layer_smoke.py -v`
  - `uv run pytest tests/ui/test_developer_console.py -v`
  - `uv run pytest tests/coding_agent/test_observability.py -v`
  - `uv run pytest tests/integration/test_durable_runtime_smoke.py -v`
  - `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
  - `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
  - `uv run pytest tests/coding_agent/evaluation/ -v`
  - `uv run ruff format --check --preview tests/coding_agent/test_bee_template_pack_smoke.py docs/bee_template_pack/USAGE.md docs/bee_template_pack/IMPLEMENTATION_REPORT.md docs/bee_template_pack/GOAL_PROGRESS.md`
  - `uv run ruff check --preview tests/coding_agent/test_bee_template_pack_smoke.py`
  - `git diff --check -- .`
- Stop criteria:
  - Smoke tests cover pack discovery, compatibility, dry-run, memory/recall,
    console visibility, observability labels, and no raw leakage.
  - Usage and implementation report docs exist.
  - Prior smoke/regression suites pass where practical.
  - No unrelated homelab/nmem/Argo/K8s/desktop/bridge/multi-agent work is
    introduced.

### After

- Changed files:
  - `docs/bee_template_pack/GOAL_PROGRESS.md`
  - `docs/bee_template_pack/USAGE.md`
  - `docs/bee_template_pack/IMPLEMENTATION_REPORT.md`
  - `tests/coding_agent/test_bee_template_pack_smoke.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_bee_template_pack_smoke.py -v`
  - `uv run pytest tests/coding_agent/test_cross_topic_memory_smoke.py -v`
  - `uv run pytest tests/coding_agent/test_external_executor.py tests/coding_agent/test_external_executor_smoke.py -v`
  - `uv run pytest tests/coding_agent/test_bee_launch.py tests/coding_agent/test_bee_command_bridge.py -v`
  - `uv run pytest tests/coding_agent/test_bee_workspace.py tests/coding_agent/test_bee_runtime.py -v`
  - `uv run pytest tests/coding_agent/test_scheduled_runs.py tests/coding_agent/test_topic_layer_smoke.py -v`
  - `uv run pytest tests/ui/test_developer_console.py tests/coding_agent/test_observability.py -v`
  - `uv run pytest tests/integration/test_durable_runtime_smoke.py tests/coding_agent/test_context_system_smoke.py -v`
  - `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py tests/coding_agent/evaluation/ -v`
  - `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
  - `uv run ruff format --check --preview tests/coding_agent/test_bee_template_pack_smoke.py docs/bee_template_pack/USAGE.md docs/bee_template_pack/IMPLEMENTATION_REPORT.md docs/bee_template_pack/GOAL_PROGRESS.md`
  - `uv run ruff check --preview tests/coding_agent/test_bee_template_pack_smoke.py`
  - `git diff --check -- .`
- Results:
  - Bee template pack smoke passed: 5 passed.
  - Cross-topic memory smoke passed: 1 passed.
  - External executor tests passed: 29 passed.
  - Bee launch and command bridge tests passed: 67 passed.
  - Bee workspace and Bee runtime tests passed: 109 passed.
  - Scheduled runs and topic layer smoke passed: 21 passed.
  - Developer Console and observability tests passed: 83 passed.
  - Durable runtime and context system smokes passed: 7 passed.
  - Action safety smoke and evaluation suite passed: 21 passed.
  - Release verification AgentKit pipeline gate passed: 8 passed,
    29 deselected.
  - Ruff format check, smoke-test ruff check, and git whitespace check passed.
  - Initial smoke test failed on unsafe `script_note` fixture metadata, proving
    commands.yaml static safety checks were active; fixture was changed to safe
    metadata and rerun green.
- Remaining risks:
  - Full production-module `ruff check --preview` remains outside this goal due
    to pre-existing lint debt documented in G152.
  - First real external pack dogfood remains deferred to the next phase.
