# ADR-0047: Define Bee template pack and domain pack integration boundaries

**Status**: Accepted
**Date**: 2026-05-24

## Context

ADR-0042 defines workspace-local `.bee/templates` and sanitized `.bee/runs`
artifacts. ADR-0043 keeps `commands.yaml` as non-executing command intent
metadata. ADR-0044 adds Bee launch surfaces. ADR-0045 adds executor adapter
boundaries. ADR-0046 adds topic-derived memory and cross-topic recall.

The next gap is consuming external collections of Bee templates, such as a
future homelab pack, without turning those packs into platform code. Coding
Agent needs a generic way to discover template packs, validate their manifests
and templates, produce compatibility reports, preview dry-run launches, and
surface pack state in the console and metrics. That generic contract must not
hard-code homelab, NetBird, OCI, Argo CD, nmem, Kubernetes, or any specific
infrastructure domain.

## Decision

Keep Bee template pack integration in `src/coding_agent/` as product-layer
runtime behavior. AgentKit Core remains generic and must not gain Bee pack,
domain pack, homelab, nmem, or infrastructure-specific primitives.

Define `BeeTemplatePack`:

- A generic collection of Bee workspace templates plus pack-level metadata.
- Has a safe `pack_id`, name, version, source, root path, domain profile,
  tags, default policies, and loaded `BeeWorkspaceTemplate` records.
- Is data/config, not platform code.
- Must not execute commands during discovery, compatibility validation, or
  dry-run launch planning.

Define `BeePackManifest`:

- A manifest loaded from `bee-pack.yaml`, `bee-pack.json`, `.bee/pack.yaml`, or
  `.bee/pack.json`.
- Declares pack id, name, version, optional description, optional
  `DomainProfile`, referenced templates, optional default workspace/topic/memory
  policies, tags, and bounded safe metadata.
- May be absent; a workspace with `.bee/templates` can be treated as an
  implicit local pack when template discovery is otherwise safe.
- Cannot grant execution permission, override command policy, override
  workspace/path policy, bypass validation policy, bypass HITL, or weaken
  no-leak rules.

Define `BeeTemplatePackSource`:

- The origin of a pack, initially `local_workspace`, `fixture`, or `imported`.
- Source type is low-cardinality and may be used in metrics.
- Pack ids, template ids, file paths, workspace paths, task ids, topic ids, and
  launch ids must not be Prometheus labels.

Define `BeePackRegistry`:

- A product-layer registry that discovers local pack roots, loads manifests,
  registers pack metadata, lists packs, lists templates by pack, and loads a
  template through pack context.
- Preserves pack/template provenance for dry-run plans, compatibility reports,
  launch metadata, memory candidates, topic range indexing, and console views.
- Does not execute commands and does not create Bee tasks by itself.

Define `BeePackCompatibilityReport`:

- A deterministic, sanitized report produced from static pack artifacts.
- Includes report status `compatible`, `warning`, or `incompatible`.
- Includes checks, findings, recommended fixes, and template-level summaries.
- Validates pack manifest shape, template schema, `SKILL.md`, feature files,
  `commands.yaml` intent shape, `command_ref` references, node dependencies,
  acceptance criteria, risk profile, report output contract, optional memory
  candidate contract, executor kind support, and forbidden raw key usage.
- Never executes commands or executor plans.

Define `BeePackDryRunPlan`:

- A non-durable launch preview for a given pack id, template id, and input
  binding.
- Validates inputs, topic policy, workspace policy, command intent resolution,
  and executor capability availability at the capability level only.
- Previews what would be created: launch metadata, topic policy, task, task.json
  path, nodes, command intents, expected report/evidence paths, and memory
  candidate paths.
- Must not create a durable BeeTask, write task artifacts, or execute commands.
  Durable task creation remains owned by the existing Bee launch path.

Define `DomainProfile`:

- Optional bounded metadata describing a pack's generic domain shape, such as
  `operations`, `maintenance`, `testing`, or `project_workflow`.
- May influence safe memory candidate tags/profile and recall ranking.
- Must remain generic. DomainProfile is not a switch for hard-coded business
  logic.

`commands.yaml` remains intent metadata only. A pack may declare command intents
and templates may reference them, but a pack cannot make an intent executable.
Node execution still goes through Bee launch, command bridge, command policy,
workspace policy, path policy, approval/HITL, validation, executor adapters,
sanitization, evidence, and Bee acceptance gates.

Pack memory candidates remain candidates until reviewed through the existing
memory review flow. Accepted memory stays reference-only and must retain
topic/task/run/evidence/report plus pack/template provenance when available.

Observability boundaries:

- Metrics may use low-cardinality labels such as pack validation status, source
  type, template status, and dry-run status.
- Metrics must not use `pack_id`, `template_id`, `task_id`, `topic_id`,
  `launch_id`, `run_id`, `session_id`, `node_id`, file path, command, prompt,
  content, output, or secret labels.
- Traces may include safe correlation ids where the existing privacy contract
  allows them, but must not include raw prompts, messages, content, result text,
  stdout/stderr, env, command output, raw logs, or secrets.

Console boundaries:

- Console may show pack list, pack detail, template list by pack,
  compatibility report, dry-run launch plan preview, and linked
  topics/tasks/memory where available.
- Console must not render raw logs, raw command output, stdout/stderr, env,
  prompt, content, messages, result text, credentials, or secrets.

nmem sync, Argo CD integration, production Kubernetes/Argo executor hardening,
homelab-specific adapters, desktop, bridge, and multi-agent work are deferred.

## Alternatives Rejected

- Hard-code homelab templates or infrastructure logic in Coding Agent. Rejected
  because template packs must be generic data/config and homelab behavior belongs
  in external pack content or later domain adapters.
- Treat pack manifests as executor manifests. Rejected because packs cannot
  bypass Bee launch, command bridge, policy, approval, validation, executor,
  evidence, or acceptance contracts.
- Execute `commands.yaml` or static pack commands during validation. Rejected
  because validation must be deterministic and non-executing.
- Auto-promote pack memory candidates to accepted memory. Rejected because all
  memory reuse must preserve review and reference-only semantics.
- Require nmem, hosted services, Docker, Kubernetes, Argo Workflows, or real LLM
  calls. Rejected because this phase must remain local, deterministic, and
  credential-free.
- Put BeeTemplatePack primitives in AgentKit Core. Rejected because Bee packs
  are Coding Agent product-layer packaging behavior.
- Integrate with Argo CD in this phase. Rejected because Argo CD manages GitOps
  desired state and is outside the generic Bee template pack contract.

## Acceptance Criteria

- [ ] `test_bee_pack_manifest_loads_valid_manifest`
- [ ] `test_bee_pack_manifest_missing_manifest_creates_implicit_local_pack`
- [ ] `test_bee_pack_manifest_rejects_missing_template`
- [ ] `test_bee_pack_registry_discovers_local_and_fixture_packs`
- [ ] `test_bee_pack_registry_preserves_pack_template_provenance`
- [ ] `test_bee_pack_compatibility_reports_compatible_pack`
- [ ] `test_bee_pack_compatibility_reports_bad_command_ref`
- [ ] `test_bee_pack_compatibility_warns_unsupported_executor`
- [ ] `test_bee_pack_compatibility_detects_forbidden_raw_keys`
- [ ] `test_bee_pack_dry_run_plan_validates_inputs_without_task_creation`
- [ ] `test_bee_pack_dry_run_detects_unsafe_command_intent_without_execution`
- [ ] `test_bee_pack_memory_candidate_includes_pack_template_provenance`
- [ ] `test_bee_pack_recall_filters_by_domain_profile_and_tag`
- [ ] `test_console_bee_template_packs_render_safe_reports`
- [ ] `test_bee_pack_metrics_omit_high_cardinality_labels`
- [ ] `test_bee_template_pack_e2e_smoke`
- [ ] `uv run pytest tests/coding_agent/test_bee_template_pack.py -v`
- [ ] `uv run pytest tests/coding_agent/test_bee_template_pack_smoke.py -v`
- [ ] `uv run pytest tests/ui/test_developer_console.py -v`
- [ ] `uv run pytest tests/coding_agent/test_observability.py -v`
- [ ] `git diff --check -- .`

## References

- `docs/bee_template_pack/CURRENT_STATE.md`
- `docs/bee_template_pack/GOAL_PROGRESS.md`
- `docs/adr/0042-bee-workspace-contract.md`
- `docs/adr/0043-bee-command-bridge.md`
- `docs/adr/0044-bee-launch-surfaces.md`
- `docs/adr/0045-external-executor-adapter-boundaries.md`
- `docs/adr/0046-cross-topic-memory-and-topic-range-search.md`
- `src/coding_agent/bee_workspace.py`
- `src/coding_agent/bee_runtime.py`
- `src/coding_agent/bee_launch.py`
- `src/coding_agent/bee_command_bridge.py`
- `src/coding_agent/external_executor.py`
- `src/coding_agent/topic_memory.py`
- `src/coding_agent/topic_range_index.py`
- `src/coding_agent/recall_context.py`
- `src/coding_agent/ui/developer_console.py`
- `src/coding_agent/observability.py`
