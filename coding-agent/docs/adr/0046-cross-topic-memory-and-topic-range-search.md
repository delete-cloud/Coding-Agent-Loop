# ADR-0046: Define cross-topic memory and topic range search boundaries

**Status**: Accepted
**Date**: 2026-05-23

## Context

ADR-0034 defines context-pack and memory evidence semantics. ADR-0039 defines
Topic as a Coding Agent product-layer tape range. ADR-0041 through ADR-0045 add
Bee tasks, workspace artifacts, command bridge execution, launch surfaces, and
external executor adapters.

The next gap is learning from completed work. Bee tasks now produce sanitized
reports, evidence, memory candidates, topic anchors, validation/action results,
and executor summaries. Without cross-topic recall, those records stay useful
only to the current task or console view. Future topics and Bee launches need a
deterministic way to retrieve prior topic summaries, reviewed memory, and
sanitized report/evidence references as reference context.

## Decision

Keep cross-topic memory and topic range search in `src/coding_agent/` as
product-layer runtime/context behavior. AgentKit Core remains generic and must
not gain Topic, Bee, memory review, or homelab-specific primitives.

Define `TopicRange`:

- A bounded range on an existing tape.
- Starts at a `topic_initial` product anchor.
- Ends at a `topic_finalized` or `topic_aborted` product anchor when closed.
- Carries `topic_id`, `tape_id`, `session_id`, start/end sequence, status, kind,
  title, summary, and safe metadata.
- Is provenance, not an instruction source.

Define `TopicSummary`:

- A bounded, sanitized summary for a finalized or explicitly indexed topic.
- May include topic kind/profile/status, title, summary text, tags, source entry
  ranges, and related Bee task/report/evidence references.
- Must not include raw prompt/content/message/result/secret/text, raw stdout,
  raw stderr, env dumps, command output, raw logs, or credentials.

Define `TopicRangeIndex`:

- A Coding Agent-owned index over finalized Topic summaries and sanitized Bee
  task report/evidence summaries.
- Supports deterministic local search by text query, topic kind/profile, Bee
  template ID, tags, status, and time range.
- Does not require external embedding services, production credentials, nmem,
  hosted vector stores, Docker, Kubernetes, Argo, or real LLM calls.
- Does not index open topics by default unless a caller explicitly asks for an
  in-progress diagnostic mode.

Define `TopicRecallQuery`:

- Built for a new topic, run, or Bee launch from safe metadata: title, summary,
  kind/profile, template ID, tags, and optional time bounds.
- Does not include raw prompts, messages, command output, stdout/stderr, env, or
  secret-bearing text.

Define `TopicRecallResult`:

- A ranked reference to a prior topic range, accepted memory, or sanitized Bee
  artifact.
- Contains source type, score, reason, topic/task/run/evidence provenance, source
  ranges, and bounded safe summary.
- Recall results are reference evidence only; they are not commands, policies,
  approvals, or system instructions.

Define `TopicRecallAnchor`:

- A product anchor written through existing tape anchor mechanisms when recall
  is committed.
- Persists a `topic_recall_links` row or equivalent durable link.
- May include safe score buckets/reasons, but not raw query text or raw evidence
  bodies.

Define `TopicDerivedMemory`:

- A memory candidate derived from a finalized Topic, Bee report, sanitized
  evidence summary, validation/action outcome, or existing
  `memory_candidates.yaml` entry.
- Must retain topic/task/run/evidence/report/source-range provenance.
- Starts as `candidate`; it is not accepted by default and is not rendered as
  authoritative instruction.

Define `MemoryCandidateReview`:

- Product-layer review lifecycle for candidate memory:
  `candidate`, `accepted`, `rejected`, and `archived`.
- Accept, reject, and archive operations are idempotent.
- Review records keep reviewer/reason metadata only when sanitized.
- Review does not call nmem or external memory backends.

Define `AcceptedMemory`:

- Evidence-backed reference memory approved through review.
- Must retain topic/task/run/evidence/report/source-range provenance.
- Can be retrieved by context building and rendered through `ContextPack` as
  reference-only material.
- Must never become system instruction, policy override, action approval, or
  command authorization.

Define `RecallContextPack`:

- A context pack section that combines ranked `TopicRecallResult` and
  `AcceptedMemory` references.
- Injected through existing `build_context` hooks or product-layer context
  composition without rewriting the AgentKit pipeline.
- Uses `ContextPackRenderer` reference semantics and explicitly preserves
  no-leak behavior.

Bee memory candidates remain candidates until reviewed. Bee report/evidence
summaries may feed indexing and candidate generation only after sanitization.
Recall results cannot bypass approval, command policy, workspace policy, path
policy, validation policy, HITL, or Bee acceptance gates.

Observability boundaries:

- Metrics may use low-cardinality labels such as recall source, status, memory
  kind, and review status.
- Metrics must not use `memory_id`, `topic_id`, `task_id`, `run_id`,
  `session_id`, `node_id`, file path, command, prompt, content, output, or
  secret labels.
- Traces may include safe correlation IDs where existing privacy contracts allow
  them, but must not include raw prompts, messages, content, result text,
  stdout/stderr, env, command output, raw logs, or secrets.

Console boundaries:

- Console may show memory candidate inboxes, accepted/rejected/archived memory,
  memory provenance, topic recall links, Bee recall links, and recall evidence
  summaries.
- Console must not render raw logs, raw command output, stdout/stderr, env,
  prompt, content, messages, result text, credentials, or secrets.
- Console review actions are allowed only through safe product APIs that preserve
  review state and provenance; otherwise the MVP stays read-only.

External memory backends such as nmem are deferred. Homelab-specific memories,
Argo CD, production Kubernetes/Argo executor work, desktop, bridge, and
multi-agent task graph work are out of scope.

## Alternatives Rejected

- Treat recalled memory as system instruction. Rejected because memory is
  evidence-backed reference context and must not override policies or approvals.
- Auto-promote Bee memory candidates to accepted memory. Rejected because
  candidates require explicit review before reuse as accepted memory.
- Index raw reports, stdout/stderr, env dumps, command output, or logs. Rejected
  because it violates the no-leak contract.
- Require external embeddings, nmem, hosted vector stores, or real LLM calls.
  Rejected because this phase must be deterministic and credential-free.
- Put TopicRange or memory review primitives in AgentKit Core. Rejected because
  they are Coding Agent product abstractions over Topic, Bee, context, and
  console behavior.
- Hard-code homelab memory kinds or infrastructure templates. Rejected because
  cross-topic memory must stay generic.

## Acceptance Criteria

- [x] `test_topic_range_index_indexes_finalized_topic_and_searches_text`
- [x] `test_topic_range_index_skips_open_topic_by_default`
- [x] `test_topic_range_index_searches_kind_profile_tag_status_and_time`
- [x] `test_topic_range_index_indexes_bee_task_topic_metadata`
- [ ] `test_topic_derived_memory_candidate_preserves_provenance`
- [ ] `test_memory_review_accept_reject_archive_idempotent`
- [ ] `test_accepted_memory_renders_as_reference_only`
- [ ] `test_topic_recall_planner_records_anchor_and_links`
- [ ] `test_recall_context_pack_contains_reference_evidence`
- [ ] `test_recall_metrics_omit_high_cardinality_labels`
- [ ] `test_console_memory_recall_renders_safe_provenance`
- [ ] `test_cross_topic_memory_e2e_smoke`
- [ ] `uv run pytest tests/coding_agent/test_topic_layer_smoke.py -v`
- [ ] `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- [ ] `uv run pytest tests/ui/test_developer_console.py -v`
- [ ] `uv run pytest tests/coding_agent/test_observability.py -v`
- [ ] `git diff --check -- .`

## References

- `docs/cross_topic_memory/CURRENT_STATE.md`
- `docs/cross_topic_memory/GOAL_PROGRESS.md`
- `docs/adr/0034-context-system-boundaries-and-evidence.md`
- `docs/adr/0039-topic-layer-tape-view-boundaries.md`
- `docs/adr/0041-bee-workflow-task-runtime.md`
- `docs/adr/0042-bee-workspace-contract.md`
- `docs/adr/0043-bee-command-bridge.md`
- `docs/adr/0044-bee-launch-surfaces.md`
- `docs/adr/0045-external-executor-adapter-boundaries.md`
- `src/coding_agent/topic_store.py`
- `src/coding_agent/topic_recall.py`
- `src/coding_agent/topic_provenance.py`
- `src/coding_agent/context_pack.py`
- `src/coding_agent/plugins/memory.py`
- `src/coding_agent/bee_workspace.py`
- `src/coding_agent/bee_runtime.py`
- `src/coding_agent/bee_launch.py`
- `src/coding_agent/ui/developer_console.py`
- `src/coding_agent/observability.py`
