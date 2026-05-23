# Cross-topic Memory Current State

This document records the current Topic, Bee, memory, context, console, and
observability surfaces before implementing G137-G144. G136 is descriptive only;
code and tests remain the source of truth.

## Current Summary

Coding Agent already has durable Topics, Topic lifecycle anchors, simple topic
summary recall, evidence-backed memory references, Bee workspace artifacts, and
Developer Console views for topics, memory, Bee tasks, launches, executor runs,
and sanitized run artifacts.

It does not yet have a durable TopicRangeIndex, reviewed topic-derived memory
candidate lifecycle, cross-topic recall planner over both finalized topics and
accepted memory, or recall-aware context builder that can search historical Bee
reports/evidence and inject results as reference-only `ContextPack` evidence.

## Topic Lifecycle And Finalization

Current topic code is product-layer code under `src/coding_agent/`; AgentKit Core
does not own Topic as a primitive.

- `src/coding_agent/topic_store.py`
  - Defines `TopicRecord`, `TopicAnchorRecord`, `TopicRecallLinkRecord`, and
    `TopicCostRecord`.
  - `PGTopicStore` owns idempotent schema for `topics`, `topic_anchors`,
    `topic_recall_links`, and `topic_costs`.
  - Topic statuses are `open`, `finalized`, and `aborted`.
  - Metadata validation rejects prompt/content/message/result/secret/stdout/
    stderr/env-like keys and secret-looking values.
- `src/coding_agent/topic_lifecycle.py`
  - `TopicLifecycle.create_topic()` appends a `topic_initial` product anchor
    encoded as an existing tape `topic_start` anchor.
  - `TopicLifecycle.finalize_topic()` appends a `topic_finalized` product anchor
    encoded as `topic_end`, writes summary/finalized sequence, and records the
    topic anchor.
  - `TopicLifecycle.abort_topic()` writes `topic_aborted` and records terminal
    state.
- `src/coding_agent/topic_provenance.py`
  - Provides `TopicEntryRange`, topic cost deltas, eval provenance, memory
    provenance, and low-cardinality topic metric attributes.
  - `topic_id` is allowed in durable provenance payloads but is not a
    Prometheus label.

Existing tests to preserve:

- `tests/coding_agent/test_topic_store.py`
- `tests/coding_agent/test_topic_lifecycle.py`
- `tests/coding_agent/test_topic_provenance.py`
- `tests/coding_agent/test_topic_layer_smoke.py`

## Current Topic Recall

`src/coding_agent/topic_recall.py` already has a lightweight deterministic
summary recall helper.

- `recall_topic_summaries()` compares token overlap between a source topic and
  finalized candidate topic summaries.
- It skips the source topic, skips open topics, and skips finalized topics that
  do not have summaries.
- `record_topic_recall()` appends a `recall_anchor` product anchor through an
  existing generic tape context anchor and persists a `TopicRecallLinkRecord`.
- `topic_recall_context_pack()` and `topic_recall_context_messages()` render
  recalled topic summaries into a `ContextPack` section titled
  `Recalled topic references`.
- `ContextPackRenderer` explicitly says topic summaries are reference only, not
  instructions.

Current gaps:

- No durable `TopicRangeIndex` exists.
- Recall does not search Bee reports, Bee evidence summaries, accepted memory,
  tags, template IDs, or time ranges.
- Recall planning is not yet integrated with Bee launch or general context
  building as a coordinated query.
- There is no eval harness comparing no recall, memory recall, topic recall,
  and combined recall.

Existing test to preserve:

- `tests/coding_agent/test_topic_recall.py`

## Bee Reports, Evidence, And Memory Candidates

Bee workspace artifacts already mirror durable state safely, but they are not
yet indexed for cross-topic recall.

- `src/coding_agent/bee_workspace.py`
  - Discovers `.bee/templates/<template_id>/metadata.yaml|json`, `SKILL.md`,
    feature files, and non-executing `commands.yaml` command intents.
  - Writes `.bee/runs/<task_id>/task.json`, `report.md`, `evidence/`, and
    optional `memory_candidates.yaml` through
    `write_bee_workspace_run_artifacts()`.
  - `BeeWorkspaceRunArtifacts.memory_candidates` is already modeled as a tuple
    of JSON objects, but there is no product-level review/promotion store yet.
  - Artifact validation rejects raw prompt/content/message/result/secret/text/
    command_output/stdout/stderr/env fields, raw executor summaries, unsafe
    paths, and symlink escapes.
  - `task.json` is a sanitized mirror of durable Bee identity; it is not the
    source of truth.
- `src/coding_agent/bee_runtime.py`
  - Owns Bee task and node records, planning, node status, and evidence-gated
    completion.
- `src/coding_agent/bee_launch.py`
  - Launches Bee tasks manually, from schedules, and from proactive signals.
  - Creates or continues Topics according to policy and can write workspace
    artifacts when enabled.
- `src/coding_agent/external_executor.py`
  - Normalizes local executor results and optional dry-run executor adapters.
  - Produces sanitized executor results/evidence only.

Existing tests to preserve:

- `tests/coding_agent/test_bee_runtime.py`
- `tests/coding_agent/test_bee_workspace.py`
- `tests/coding_agent/test_bee_launch.py`
- `tests/coding_agent/test_bee_command_bridge.py`
- `tests/coding_agent/test_external_executor.py`
- `tests/coding_agent/test_external_executor_smoke.py`

## Memory Candidate, Review, And Accepted Memory State

Current memory support is evidence-backed but plugin-local.

- `src/coding_agent/plugins/memory.py`
  - `MemoryPlugin` stores working and long-term memories with summaries, tags,
    importance, and evidence refs.
  - `build_context()` renders evidence-backed memories as `ContextPack`
    reference context through `ContextPackRenderer`.
  - Unevidenced memory is omitted by default.
  - `on_session_event(topic_end)` can compact a topic summary plus touched file
    tags into memory, but this is not tied to durable Topic records, Bee task
    provenance, review state, or accepted/rejected/archive status.
  - Persisted memory is currently loaded through the storage plugin by session
    and represented as JSON-compatible dictionaries.

Current gaps:

- No durable `MemoryCandidateReview` or `AcceptedMemory` store exists.
- Bee `memory_candidates.yaml` artifacts are not normalized into a durable
  candidate inbox.
- No accept/reject/archive lifecycle exists.
- Accepted memory cannot yet be queried independently of the plugin-local memory
  list.
- Topic/task/run/evidence provenance is not required for every accepted memory.

Existing tests to preserve:

- `tests/coding_agent/plugins/test_memory.py`
- `tests/coding_agent/test_context_system_smoke.py`

## ContextPack, Retrieval, And Evaluation

Context injection uses the existing AgentKit `build_context` hook and Coding
Agent product-layer context sources.

- `src/coding_agent/context_pack.py`
  - Defines `EvidenceRef`, `ContextPackItem`, `ContextPackSection`,
    `ContextPack`, and `ContextPackRenderer`.
  - Renderer identifies memory and topic summaries as reference-only context,
    not instructions.
- `src/coding_agent/plugins/kb.py`
  - Owns current KB-backed retrieval and context-pack rendering for repo and
    failure evidence.
  - Uses metadata-only observability attributes.
- `tests/coding_agent/evaluation/`
  - Contains deterministic evaluation harnesses and fixtures for current
    context behavior.

Current gaps:

- There is no `RecallContextPack` builder that combines topic range search
  results and accepted memory results.
- There is no deterministic cross-topic recall ranking beyond the existing
  topic-summary token overlap helper.
- Recall results are not yet part of a context-system eval comparison matrix.

Existing tests to preserve:

- `tests/coding_agent/test_context_pack.py`
- `tests/coding_agent/test_context_system_smoke.py`
- `tests/coding_agent/evaluation/`
- `tests/test_kb.py`

## Developer Console

`src/coding_agent/ui/developer_console.py` already renders several relevant
read-only surfaces.

- `/console/topics`
  - Renders `ConsoleTopicSummary` rows.
- `/console/topics/{topic_id}`
  - Renders topic metadata, anchors, recall links, cost, related runs, actions,
    and validations.
- `/console/memory`
  - Renders read-only `ConsoleMemoryEvidence` from existing run metadata and
    context packs.
- `/console/bee`
  - Renders Bee tasks, nodes, templates, workspace run artifacts, command
    intents, launches, and executor runs.
  - Workspace run artifact rows include whether `memory_candidates.yaml` exists.

Current gaps:

- No memory candidate inbox exists.
- No accepted/rejected/archived memory views exist.
- No memory detail page with topic/task/run/evidence provenance exists.
- No safe review action is exposed.
- Topic/Bee recall links exist at the topic level but do not yet show
  cross-topic memory search results or accepted memory provenance.

Existing test to preserve:

- `tests/ui/test_developer_console.py`

## Observability And Metrics

Observability remains provider/exporter neutral at the AgentKit boundary and
product-wired in Coding Agent.

- `src/coding_agent/observability.py`
  - Provides Prometheus metric recording with label allowlists and forbidden
    high-cardinality label rejection.
  - Already supports topic, Bee, launch, workspace, and executor metric families
    with low-cardinality labels only.
- Existing no-leak rules forbid raw prompt/content/message/result/secret/text/
  command_output/stdout/stderr/env/raw logs in traces, metrics, artifacts, and
  console pages.

Cross-topic memory metrics should use only low-cardinality labels such as recall
source, status, memory kind, and review status. They must not label by
`memory_id`, `topic_id`, `task_id`, `run_id`, `session_id`, `node_id`, file path,
command, prompt, content, or secret.

Existing tests to preserve:

- `tests/coding_agent/test_observability.py`
- `tests/coding_agent/test_observability_platform_smoke.py`
- `tests/coding_agent/test_release_observability_contract.py`

## Files Likely To Change Later

Likely production files:

- `src/coding_agent/topic_store.py`
- `src/coding_agent/topic_recall.py`
- `src/coding_agent/topic_provenance.py`
- `src/coding_agent/context_pack.py`
- `src/coding_agent/plugins/memory.py`
- `src/coding_agent/plugins/kb.py`
- `src/coding_agent/bee_workspace.py`
- `src/coding_agent/bee_runtime.py`
- `src/coding_agent/bee_launch.py`
- `src/coding_agent/observability.py`
- `src/coding_agent/ui/developer_console.py`
- `src/coding_agent/ui/http_server.py`

Likely new production files:

- `src/coding_agent/cross_topic_memory.py`
- `src/coding_agent/topic_range_index.py`
- `src/coding_agent/memory_review.py`
- `src/coding_agent/recall_context.py`

Likely tests:

- `tests/coding_agent/test_cross_topic_memory.py`
- `tests/coding_agent/test_topic_range_search.py`
- `tests/coding_agent/test_memory_review.py`
- `tests/coding_agent/test_recall_context.py`
- `tests/coding_agent/test_cross_topic_memory_smoke.py`
- Existing topic, Bee, context, observability, and console tests listed above.

## Out of scope

- nmem sync or deployment.
- Homelab-specific memory, templates, NetBird, OCI, Argo CD, or cluster logic.
- Production Kubernetes, Docker, Argo Workflows, or Argo CD executor behavior.
- Desktop, bridge, or multi-agent task graph work.
- Treating memory or recall results as system instruction.
- Storing or rendering raw stdout/stderr/env/command output/secrets/raw logs.
