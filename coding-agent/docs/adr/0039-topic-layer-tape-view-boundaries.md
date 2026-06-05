# ADR-0039: Define Topic layer and tape view boundaries

**Status**: Accepted
**Date**: 2026-05-21

**Supersession note (2026-06-05)**: The legacy file-overlap `TopicPlugin`
referenced below has since been removed from the default Coding Agent
application path. Durable Topic lifecycle now uses `TopicLifecycle` and
product-layer stores; `topic_start`/`topic_end` encoded anchors remain
supported, but inferred `topic_start`/`topic_end` session events are no longer
emitted by default.

## Context

The repository already has durable sessions, durable runs, stable tape timelines, context packs, memory evidence, evaluation fixtures, action safety records, observability, developer console pages, dogfood evidence, and workspace providers. G77 also found an older `TopicPlugin` in Coding Agent that detects file-overlap topic shifts, writes `topic_start` and `topic_end` anchors, and emits session events, but it does not provide durable topic records, searchable topic ranges, recall links, topic status, topic cost aggregates, or safe console-ready topic views.

The next phase needs a Topic layer that makes long-lived work units inspectable without changing AgentKit Core. The main boundary risk is turning Topic into a generic AgentKit primitive or conflating it with Session, Run, or Workspace. The second risk is leaking raw user prompt/message/content through topic anchors, events, tracing attributes, metrics labels, durable records, or console pages. Existing `TopicPlugin` behavior that stores a truncated first user message in `topic_start` anchor payloads must be migrated, sanitized, or bypassed before new topic views expose anchors.

Topic support also needs to prepare extension points for later Schedule and Bee workflows. Those future systems will need to bind work to a topic, but this phase must not implement schedule execution, Bee DAG runtime, external executors, desktop app, bridge, proactive agent behavior, or multi-agent task graphs.

## Decision

Keep Topic as a Coding Agent product/runtime/context abstraction, not an AgentKit Core primitive.

- AgentKit Core continues to own generic tape entries, anchors, tape views, storage protocols, runtime hooks, and observability abstractions.
- Coding Agent owns durable Topic records, topic store/schema, topic lifecycle APIs, topic recall, topic provenance, topic cost aggregation, console topic pages, and topic observability wiring.
- The AgentKit pipeline must not be rewritten for Topic. Topic operations must use existing app/runtime extension points and tape append/search behavior.
- Existing G00-G76 durable runtime, context, action safety, release, observability, console, dogfood, and workspace contracts remain intact. Topic fields/views are additive.

Define Topic as a business context unit over a tape range.

- A Topic is a durable Coding Agent record that describes a bounded unit of work on a stable tape.
- Session is not Topic.
- Run is not Topic.
- Workspace is not Topic.
- A Session may contain zero or more Topics.
- A Run may occur inside a Topic range or contribute to a Topic, but run lifecycle remains independent.
- A Workspace may be linked through session/run metadata, but workspace provisioning is not part of Topic.

Define TopicRange as a tape range bounded by lifecycle anchors.

- `TopicRange` identifies a range on one `tape_id`.
- `topic_initial` is the product-level lifecycle anchor that marks the start of the topic range.
- `topic_finalized` is the product-level lifecycle anchor that marks the normal finalized end of the topic range.
- `topic_aborted` may be represented as a finalizing structural anchor or explicit durable status when a topic closes without normal finalization.
- Existing AgentKit anchor compatibility may continue mapping public `topic_initial` / `topic_finalized` terminology to generic `topic_start` / `topic_end` anchor types, but durable Topic APIs and docs should use product-level names.
- Topic records store `topic_initial_seq` and optional `topic_finalized_seq` so old tapes without topic anchors remain loadable and searchable.
- Topic operations must not write high-frequency runtime events.

Define recall as an explicit relationship between topics.

- `recall_anchor` is a structural tape anchor written when a topic recalls another topic or a recalled topic summary is injected into context.
- `recall_anchor` is product-level terminology encoded through the existing generic anchor shape, such as an `anchor_type="context"` anchor with safe topic recall metadata. It must not require adding a new AgentKit Core anchor type in this phase.
- Topic recall links are durable records from source topic to recalled topic with low-cardinality relationship metadata and optional source entry ranges.
- Topic recall initially uses deterministic matching or existing retrieval infrastructure. It must not require external hosted services or real LLM calls.
- Recalled topic summaries are reference evidence, not policy or system instructions.

Define topic state and summary fields.

- Topic status is one of `open`, `finalized`, or `aborted`.
- Topic summary is a sanitized, bounded summary suitable for durable records and console display.
- Topic summary must not store raw prompts, raw messages, raw command output, stdout, stderr, env, secrets, or unbounded text.
- Topic title and owner are product metadata and must be sanitized, bounded display fields.
- Topic titles must not be derived directly from raw prompts, messages, command output, stdout, stderr, env, or secrets.
- Topic title and owner must not be Prometheus labels.
- Topic metadata must be JSON-safe and must not include raw sensitive content.

Define topic cost as an aggregate.

- Topic cost is a durable aggregate of available usage, tool/action/validation/run counts, and related totals by `topic_id`.
- Topic cost aggregation may store `topic_id` in product durable records.
- Prometheus metrics must not use `topic_id`, `run_id`, `session_id`, `workspace_id`, file path, command, prompt, content, or secret as labels.
- Topic metrics may use low-cardinality labels such as topic kind, status, or profile.

Define ContextPack as a constructed Tape/Repo/Memory View.

- ContextPack remains a Coding Agent app-level structure for selected reference evidence.
- A ContextPack may include topic-derived metadata such as `source_topic_ids` and `source_entry_ranges` where available.
- ContextPack construction can use tape ranges, repo evidence, memory evidence, failure evidence, and recalled topic summaries.
- ContextPack rendering remains reference grounding and must not turn memory or recalled topic summaries into instructions.
- The existing `build_context` hook remains the integration point.

Define Memory as evidence-backed reference context that can later become topic-derived.

- Memory remains reference-only and evidence-backed.
- Memory evidence may later reference `topic_id` and topic entry ranges when available.
- Topic-derived memory does not become system policy or an instruction source.
- Existing memory records without topic provenance remain loadable.

Preserve and migrate existing `TopicPlugin` behavior deliberately.

- G79-G84 must inspect `src/coding_agent/plugins/topic.py` before implementing lifecycle behavior.
- Durable Topic lifecycle should reuse, migrate, or supersede the plugin-local topic detector instead of adding conflicting parallel lifecycle anchors.
- Existing raw user-message snippets in `topic_start` anchor payloads and session event labels are a known privacy risk. New durable topic anchors/events must use safe bounded labels and metadata only.
- Existing `topic_end` structural boundary semantics and `meta.skip` behavior should be preserved unless a targeted compatibility test proves a safer replacement.

Schedules and Bee workflows bind to Topic later but are out of scope now.

- Future schedules may reference a `topic_id` to resume or proactively continue work.
- Future Bee workflows may use Topic as the business context unit for DAG tasks.
- This phase only prepares durable identifiers, ranges, recall links, provenance, and safe views.
- This phase does not implement schedule triggers, Bee DAG runtime, external executors, desktop app, bridge, proactive agent loops, or multi-agent task graphs.

## Alternatives Rejected

- Make Topic an AgentKit Core primitive. Rejected because Topic is product/runtime/business context for Coding Agent, while AgentKit should remain generic.
- Treat Session as Topic. Rejected because sessions are user/runtime containers and may contain multiple unrelated topics.
- Treat Run as Topic. Rejected because runs are execution attempts and may be shorter, longer, retried, failed, or nested relative to a topic.
- Store Topic only as tape anchors with no durable topic table. Rejected because list, status, recall, cost, console, and provenance need durable indexed records.
- Build Topic as a schedule or Bee workflow feature. Rejected because those systems are future phases and would expand the scope beyond tape view foundation.
- Store raw prompts/messages as topic titles, summaries, anchors, traces, metrics, or console fields. Rejected because it violates the no-leak boundary and repeats an existing `TopicPlugin` privacy risk.
- Add `topic_id` as a Prometheus label. Rejected because it is high cardinality.

## Acceptance Criteria

Implementation of G79-G84 should add executable tests covering these contracts:

- [ ] `test_topic_store_schema_is_idempotent`
- [ ] `test_topic_create_finalize_abort_and_list`
- [ ] `test_create_topic_writes_topic_initial_anchor`
- [ ] `test_finalize_topic_writes_topic_finalized_anchor`
- [ ] `test_topic_range_lists_entries_between_anchors`
- [ ] `test_old_tapes_without_topic_anchors_still_work`
- [ ] `test_topic_recall_writes_recall_anchor_and_link`
- [ ] `test_context_pack_includes_recalled_topic_metadata_when_enabled`
- [ ] `test_topic_cost_aggregates_usage_without_prometheus_topic_id_label`
- [ ] `test_topic_console_list_and_detail_render_safe_summaries`
- [ ] `test_topic_layer_smoke_lifecycle_recall_context_console`
- [ ] `uv run pytest tests/coding_agent/ -k "topic or context_pack or memory or observability" -v`
- [ ] `uv run pytest tests/ui/test_developer_console.py -v`
- [ ] `git diff --check -- .`

## References

- `docs/topic_layer/CURRENT_STATE.md`
- `docs/topic_layer/GOAL_PROGRESS.md`
- `docs/adr/0003-http-sessions-use-one-stable-tape-timeline.md`
- `docs/adr/0029-durable-runtime-identity.md`
- `docs/adr/0033-postgresql-tape-debug-queries.md`
- `docs/adr/0034-context-system-boundaries-and-evidence.md`
- `docs/adr/0036-observability-backend-exporter-boundaries.md`
- `docs/adr/0037-developer-console-debug-ui.md`
- `src/agentkit/tape/models.py`
- `src/agentkit/tape/tape.py`
- `src/agentkit/tape/view.py`
- `src/agentkit/storage/protocols.py`
- `src/agentkit/storage/pg.py`
- `src/coding_agent/plugins/topic.py`
- `src/coding_agent/plugins/memory.py`
- `src/coding_agent/context_pack.py`
- `src/coding_agent/ui/session_manager.py`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/ui/developer_console.py`
- `src/coding_agent/observability.py`
