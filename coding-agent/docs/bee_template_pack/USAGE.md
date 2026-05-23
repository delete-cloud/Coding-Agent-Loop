# Bee Template Pack Usage

Bee template packs are generic collections of Bee workspace templates. They are
loaded as data/config from a workspace or external repository; platform code must
not hard-code a domain such as homelab, nmem, Argo CD, Kubernetes, OCI, or
NetBird.

## Layout

Supported pack manifests:

```text
bee-pack.yaml
bee-pack.json
.bee/pack.yaml
.bee/pack.json
```

Recommended workspace layout:

```text
bee-pack.yaml
.bee/templates/<template_id>/
.bee/runs/
.bee/memory/
```

If no manifest exists, a workspace with `.bee/templates/` is treated as an
implicit local pack when its templates are safe to load.

## Manifest

Minimal YAML manifest:

```yaml
pack_id: pack-alpha
name: Alpha Pack
version: 1.0.0
domain_profile: maintenance
templates:
  - backup-check
tags:
  - local
  - backup
metadata:
  owner: platform
```

`pack_id`, `name`, `version`, and `templates` are required. Template ids must be
unique and must refer to directories under `.bee/templates/`.

## Validation

Compatibility validation is static. It loads manifests, template metadata,
`SKILL.md`, `features/*.feature`, and `commands.yaml` intent metadata. It does
not execute commands, create Bee tasks, contact external services, or grant
execution permission.

The validator checks:

- manifest and template schema
- `SKILL.md` and acceptance features
- `commands.yaml` intent metadata
- `command_ref` references
- node dependencies
- risk/report/memory candidate contracts
- executor kind support
- forbidden raw/sensitive keys in static artifacts

## Dry Run

Dry-run launch planning previews what the existing Bee launch path would create:

- launch metadata
- topic and workspace policy
- task preview
- `task.json`, report, evidence, and memory candidate paths
- nodes and command intent references

Dry runs do not create durable Bee tasks and do not execute commands.

## Console And Metrics

The Developer Console Bee page can show:

- template pack list
- template list by pack
- compatibility report summary
- dry-run launch plan preview
- existing launches, tasks, executor runs, workspace templates, and artifacts

Prometheus metrics use low-cardinality labels only:

```text
bee_pack_validations_total{status,source_type}
bee_pack_templates_total{source_type,status}
bee_pack_dry_runs_total{status}
```

Do not add `pack_id`, `template_id`, `task_id`, `topic_id`, `run_id`, file paths,
commands, prompts, content, secrets, stdout, or stderr as Prometheus labels.

## Memory And Recall

Pack metadata may be attached to memory candidate provenance and topic-range
index metadata. Accepted memory remains reference-only and review-gated. External
memory backends such as nmem are deferred.
