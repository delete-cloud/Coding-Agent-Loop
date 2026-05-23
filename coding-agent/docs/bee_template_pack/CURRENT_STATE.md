# Bee Template Pack / Domain Pack Current State

G145 maps the current repository state before adding generic Bee template pack
and domain pack integration. This goal is documentation-only and does not
change production code.

## Scope

The next phase should let Coding Agent discover, validate, dry-run, and later
launch external `.bee` template packs without hard-coding any infrastructure
domain. A BeeTemplatePack is a generic collection of Bee templates plus pack
metadata. It must not execute commands during discovery, compatibility
validation, or dry-run planning, and it must not grant execution permissions.

Out of scope:

- Homelab-specific logic, NetBird, OCI, backup, or restore behavior.
- nmem sync or any external memory backend.
- Argo CD integration.
- Production Kubernetes, production Argo Workflows, or required Docker.
- Desktop, bridge, or multi-agent integration.

## Current `.bee/templates` Discovery

Current workspace-local Bee template support is implemented in
`src/coding_agent/bee_workspace.py`.

- `discover_bee_workspace_templates(workspace_root)` resolves
  `<workspace_root>/.bee/templates`, rejects symlinked template directories, and
  returns sorted `BeeWorkspaceTemplate` records.
- `load_bee_workspace_template(workspace_root, template_id)` loads one template
  by safe `template_id`.
- `BeeWorkspaceTemplate` records `template_id`, `template_dir`,
  `metadata_path`, sanitized `metadata`, `SKILL.md`, `features/*.feature`, and
  optional `commands.yaml`.
- Discovery currently assumes a single workspace-local `.bee/templates` root.
  There is no BeeTemplatePack, BeePackManifest, pack source, imported pack, or
  pack registry abstraction yet.
- There is no `.bee/pack.yaml`, `.bee/pack.json`, `bee-pack.yaml`, or
  `bee-pack.json` loader.
- Missing manifests are not modeled as implicit packs yet; existing code simply
  treats `.bee/templates` as workspace templates.

## Current BeeTemplate Validation

`bee_workspace.py` validates templates through existing Bee runtime contracts
instead of introducing a weaker parallel schema.

- Template ids must match the safe template id regex used by
  `_require_safe_template_id`.
- Each template must include exactly one `metadata.yaml` or `metadata.json`.
- If metadata declares `template_id`, it must match the template directory name.
- `_validate_template_metadata()` calls `parse_bee_task_manifest()` from
  `src/coding_agent/bee_runtime.py`.
- `SKILL.md` must exist.
- At least one non-symlink `features/*.feature` file must exist.
- Optional `commands.yaml` is path-checked under the template root but is not
  executed.
- Template metadata validation currently produces Bee task manifests, not pack
  compatibility reports or template-level recommended fixes.

## Current `commands.yaml` Intent Parser

`commands.yaml` remains non-executing intent metadata.

- `load_bee_workspace_command_intents(template)` parses optional
  `commands.yaml` into `BeeWorkspaceCommandIntent` objects.
- Allowed intent fields are bounded safe metadata such as `name`, `profile`,
  `policy`, `category`, `validation_label`, `status`, and metadata.
- Forbidden raw or execution-like keys are rejected by the workspace parser.
- `src/coding_agent/bee_command_bridge.py` resolves Bee node `command_ref`
  values to declared intents with `resolve_bee_command_intent()`.
- `plan_bee_command_intent()` accepts a caller-supplied command candidate and
  routes it through command policy, approval routing, workspace policy, and
  action safety. It never reads executable commands from `commands.yaml`.
- Command bridge execution and validation evidence remain separate from pack
  discovery. A future BeePackCompatibilityReport can inspect references, but it
  must not execute command strings or treat the pack as a policy grant.

## Current BeeLaunchRequest And Launch Plan Behavior

Bee launch integration lives in `src/coding_agent/bee_launch.py`.

- `BeeLaunchRequest` is the sanitized launch request for manual, schedule, and
  proactive signal sources.
- `build_bee_launch_plan(request)` resolves a workspace-local template,
  validates the manifest, loads command intent names, binds launch inputs, and
  returns `BeeLaunchPlan`.
- `BeeTemplateResolution` records template id, kind, profile, title, node ids,
  and command intent names.
- `BeeInputBinding` validates required and defaulted launch inputs.
- `BeeLaunchOrchestrator` creates `BeeLaunchRecord`, creates or continues a
  Topic, creates `BeeTaskRecord` and `BeeNodeRecord` entries, and optionally
  writes `.bee/runs/<task>/task.json`, report, and evidence directories.
- Manual, scheduled, and proactive signal launch paths reuse this same launch
  flow.
- Existing launch plans do not include `pack_id`, `pack_source`, domain profile,
  or BeePackDryRunPlan previews.
- Dry-run behavior currently means `build_bee_launch_plan()` only; there is no
  pack-aware dry-run that previews launch/topic/task/artifact paths without
  creating durable records.

## Current External Executor Boundary

External executor support is implemented in `src/coding_agent/external_executor.py`.

- Executor adapters consume already-authorized execution plans.
- Optional Docker, Kubernetes Job, and Argo Workflow adapters are disabled or
  dry-run/fake-client oriented for normal tests.
- Bee acceptance still decides node completion from sanitized evidence.
- Executor kinds are known to workspace artifact validation as `local`,
  `docker`, `kubernetes_job`, `argo_workflow`, and `fixture`.
- There is no pack compatibility check that marks unsupported executor kinds as
  warnings or deferred findings.

## Current Memory Candidate And Topic Recall Behavior

Cross-topic memory support is generic and product-layer local.

- `src/coding_agent/topic_memory.py` defines
  `TopicDerivedMemoryCandidate`, `MemoryReviewStore`, reviewed memory records,
  and accepted-memory context helpers.
- `propose_memory_candidates_from_bee_artifacts()` can create candidate memory
  from sanitized Bee run artifacts, report refs, executor evidence refs, and
  task/topic provenance.
- Candidate memory starts as `candidate`; accepted memory remains
  `reference_only`.
- `src/coding_agent/topic_range_index.py` defines `TopicRangeIndex`,
  `TopicRangeIndexDocument`, and `TopicRangeSearchQuery`.
- The index can store topic kind/status/profile/tags, a `bee_template_id`,
  related task ids, report refs, evidence refs, report summary, and evidence
  summaries.
- `src/coding_agent/recall_context.py` builds recall queries, ranks topic and
  accepted-memory references, records topic recall links, and renders recall as
  ContextPack reference evidence.
- There is no pack provenance in memory candidates, accepted memory, topic range
  documents, or recall ranking yet. Future pack integration should add safe
  `pack_id`, source type, domain profile, and pack tags where appropriate while
  keeping accepted memory reference-only.

## Current Developer Console Surfaces

Developer Console rendering lives mostly in
`src/coding_agent/ui/developer_console.py`, with HTTP wiring in
`src/coding_agent/ui/http_server.py`.

- Navigation currently includes `/console/bee`, `/console/memory`,
  `/console/topics`, `/console/schedules`, `/console/workspaces`, and
  `/console/observability`.
- `render_console_bee_page()` shows Bee tasks, nodes, workspace templates,
  run artifacts, launch records, and executor runs.
- `ConsoleBeeTemplateSummary` and `_bee_template_table()` summarize current
  workspace templates.
- `render_console_memory_page()` shows candidate/accepted/rejected/archived
  memory and provenance.
- `render_console_topic_detail_page()` shows topic recall links.
- No console route or summary type exists for Bee template packs, pack detail,
  pack compatibility reports, or pack dry-run launch plans.
- A future console extension can either add a dedicated pack section under
  `/console/bee` or add a new `/console/bee-packs` route, as long as it
  preserves the existing permission and no-leak model.

## Current Observability And Metrics

Observability helpers live in `src/coding_agent/observability.py` and are tested
by `tests/coding_agent/test_observability.py` and smoke tests.

- Existing Bee workspace metrics use low-cardinality labels such as template
  kind/profile/status and command category/policy/status.
- Bee launch metrics use low-cardinality source/status labels.
- Executor metrics use executor kind/status/capability labels.
- Cross-topic memory metrics use low-cardinality source/status/kind labels.
- Existing tests assert that ids such as template id, task id, topic id, run id,
  session id, node id, file path, command, prompt, content, and secret do not
  appear in Prometheus labels.
- There are no Bee pack validation, template pack inventory, or pack dry-run
  metrics yet.
- Future metrics should use labels like `status` and `source_type`, not
  `pack_id`, `template_id`, `task_id`, `topic_id`, paths, commands, or content.

## Exact Files And Functions To Modify Later

Likely G147-G153 production code targets:

- `src/coding_agent/bee_template_pack.py`
  - New module for `BeeTemplatePack`, `BeePackManifest`,
    `BeeTemplatePackSource`, `BeePackRegistry`,
    `BeePackCompatibilityReport`, and `BeePackDryRunPlan`.
- `src/coding_agent/bee_workspace.py`
  - Reuse `BeeWorkspaceTemplate`, `discover_bee_workspace_templates()`,
    `load_bee_workspace_template()`, `load_bee_workspace_command_intents()`,
    and artifact sanitizers.
- `src/coding_agent/bee_launch.py`
  - Extend launch planning only where needed to preserve pack/template
    provenance without changing launch semantics.
- `src/coding_agent/bee_command_bridge.py`
  - Reuse `resolve_bee_command_intent()` and non-executing command-ref checks
    for compatibility validation and dry-run planning.
- `src/coding_agent/external_executor.py`
  - Reuse executor kind and capability concepts for compatibility warnings.
- `src/coding_agent/topic_memory.py`
  - Add optional pack/template provenance to candidate creation helpers.
- `src/coding_agent/topic_range_index.py`
  - Add optional safe pack/domain metadata and recall filters or boosts.
- `src/coding_agent/recall_context.py`
  - Use pack/domain metadata when building or ranking recall queries.
- `src/coding_agent/ui/developer_console.py`
  - Add pack summary/detail rendering, compatibility report rendering, and
    dry-run plan preview rendering.
- `src/coding_agent/ui/http_server.py`
  - Wire pack console routes if HTTP-backed console views are needed.
- `src/coding_agent/observability.py`
  - Add low-cardinality Bee pack validation/template/dry-run metrics.

Likely tests:

- `tests/coding_agent/test_bee_template_pack.py`
  - Manifest loading, registry discovery, compatibility validation, dry-run
    planning, memory/recall provenance, and no-command-execution checks.
- `tests/coding_agent/test_bee_template_pack_smoke.py`
  - End-to-end pack discovery, compatibility, dry-run, memory/recall, console,
    and metrics smoke coverage.
- `tests/ui/test_developer_console.py`
  - Pack list/detail/report/dry-run rendering.
- Existing targeted tests listed below should continue to pass.

## Existing Tests To Preserve

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
- `uv run pytest tests/integration/test_durable_runtime_smoke.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/coding_agent/evaluation/ -v`
- Observability platform smoke tests.
- Workspace provider dogfood/demo tests.
- Release verification commands from
  `docs/release_hardening/release-verification.yaml` where practical.
- Scoped ruff/check for touched files.
- `git diff --check -- .`

## G145 Conclusion

The current system already has safe workspace template discovery, manifest
validation, non-executing command intent parsing, launch planning, executor
boundaries, memory candidates, topic range search, recall context, console
rendering, and low-cardinality observability. The missing layer is a generic
BeeTemplatePack contract that groups external templates, validates pack
compatibility without execution, produces dry-run launch previews, records
safe pack/template provenance, and surfaces pack state in the Developer
Console and metrics.
