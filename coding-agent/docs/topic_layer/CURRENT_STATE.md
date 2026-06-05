# Topic Layer Current State

G77 inspected the existing Tape, Run, Context, Memory, Evaluation, Developer Console, Observability, and Workspace surfaces to identify where a Coding Agent product-level Topic layer can be added without changing AgentKit Core semantics.

## Summary

- AgentKit already has generic tape entries, anchors, tape views, and optional debug queries. It does not have a first-class Topic store or Topic API.
- Current anchor types include `topic_start` and `topic_end`; deserialization accepts legacy `topic_initial` and `topic_finalized` by mapping them to those existing anchor names.
- Coding Agent no longer wires the legacy plugin-local `TopicPlugin`. Product topics are explicit durable `TopicRecord` ranges managed through `TopicLifecycle`.
- Coding Agent sessions own stable tape timelines. Runs are durable execution records that may reference a tape. A future Topic should be a range on a tape, not a replacement for Session, Run, or Tape.
- ContextPack and memory evidence already carry provenance-friendly fields, but they do not yet model topic ranges, recalled topics, or topic-derived summary evidence.
- Developer Console already has pages that can be extended with Topic list/detail views and Topic filters. It renders safe summaries rather than raw prompts, command output, stdout, stderr, or secrets.
- Observability already has no-leak tracing filters and Prometheus label allowlists. A Topic phase must not add `topic_id` as a Prometheus label.

## Tape And TapeStore

`src/agentkit/tape/tape.py` defines `Tape` as an ordered list of `Entry` objects with:

- `tape_id` and optional `parent_id`
- append, filter, snapshot, fork, JSONL save/load, and handoff operations
- `window_start`, which is derived from the last handoff anchor when loading

`Tape.fork()` creates a transient child tape with `parent_id` set to the source `tape_id`. Existing persistence commits entries back through stores instead of treating a fork as a durable topic boundary.

`src/agentkit/tape/models.py` defines:

- `Entry`, the base persisted record
- `Anchor`, a specialized `Entry` with `anchor_type` and optional `source_ids`
- `AnchorType = "handoff" | "topic_start" | "topic_end" | "fold" | "context"`

`Entry.from_dict()` promotes raw `kind == "anchor"` records into `Anchor`. It also maps:

- `topic_initial` to `topic_start`
- `topic_finalized` to `topic_end`

This means later Topic work can preserve public terminology while using the existing generic anchor shape, or explicitly decide in the ADR whether to extend anchor type names.

`src/agentkit/tape/view.py` defines `TapeView` with:

- `source_tape_id`
- selected entries
- `window_start`
- `anchor_ids`

`TapeView.from_tape()` currently builds a context window around handoff behavior. It is an existing extension point for constructed tape views, but no topic range selection exists yet.

## Tape Info And Search

`src/agentkit/storage/protocols.py` defines the narrow generic `TapeStore` protocol:

- `save(tape_id, entries)`
- `load(tape_id)`
- `list_ids()`
- `truncate(tape_id, keep)`

The optional `TapeDebugStore` extension adds:

- `info(tape_id) -> TapeInfo | None`
- `search(tape_id=None, kind=None, run_id=None, tool_call_id=None, anchor_type=None, limit=100)`

`src/agentkit/storage/pg.py` implements `PGTapeStore` over `agent_tapes(tape_id, seq, entry)`. Its search query can filter on:

- `tape_id`
- `entry.kind`
- `run_id` from `entry.meta` or `entry.payload`
- `tool_call_id` from `entry.meta` or `entry.payload`
- `anchor_type` from top-level `entry.anchor_type` or `entry.meta.anchor_type`

`src/coding_agent/plugins/storage.py` implements `JSONLTapeStore`, which supports the narrow `TapeStore` operations only. Therefore topic range query APIs should be layered so PG gets efficient debug search while JSONL/local tests can use deterministic in-memory or loaded tape scans.

Relevant ADRs:

- `docs/adr/0002-fork-tape-commit.md`
- `docs/adr/0003-http-sessions-use-one-stable-tape-timeline.md`
- `docs/adr/0033-postgresql-tape-debug-queries.md`

## Identity Model

The durable identity model is session/run/tape-based:

- `Session` in `src/coding_agent/ui/session_manager.py` stores `id`, `tape_id`, current run/turn status, origin, and optional workspace binding metadata.
- `AgentRunRecord` in `src/coding_agent/runtime_store.py` stores `run_id`, `session_id`, `tape_id`, `parent_run_id`, status, metadata, result, error, and timestamps.
- Runtime events and message snapshots are keyed by `run_id`.
- HITL interactions are keyed by `interaction_id` and include `run_id`.
- Session restore loads the stable tape from `session.tape_id`.

ADR-0029 defines durable runtime identity around `session_id`, `run_id`/`turn_id`, and `tape_id`. Topic must not be modeled as a Session or Run. It should be a business context/range on the stable tape and should be allowed to reference runs that fall inside that range.

## Removed Legacy Topic Plugin

The old `src/coding_agent/plugins/topic.py` file-overlap detector has been removed from the default application path. It was plugin-local state, not a durable Topic model, and it could write truncated user message content into `topic_start` anchor payloads and session event labels.

Current Topic work should use `src/coding_agent/topic_lifecycle.py` and `src/coding_agent/topic_store.py` instead:

- Product lifecycle anchors use safe bounded metadata.
- Durable records carry open/finalized/aborted status and tape sequence ranges.
- Recall links, topic summaries, topic cost aggregates, and topic-level provenance live in product-layer stores and helpers.
- Encoded `topic_start` and `topic_end` anchor types remain supported for compatibility with generic tape anchors and `TopicLifecycle`.
- The old plugin was also the default producer of `topic_start` and `topic_end` session events. Removing it means memory compaction and plugin-local per-topic metrics no longer trigger automatically from inferred file-overlap topics; they only run when an explicit product path emits those events.

## Context Pack And Retrieval Provenance

`src/coding_agent/context_pack.py` defines:

- `EvidenceRef(kind, source_id, label, repo_path, line_start, line_end, chunk_id, test_node_id, command_label, session_id, tape_entry_id)`
- `ContextPackItem(source_kind, source_id, label, body, rank, score, repo_path, line_start, line_end, evidence, metadata)`
- `ContextPackSection`
- `ContextPack`
- `ContextPackRenderer`

Memory items are explicitly rendered as reference-only content and can be omitted if they lack evidence.

`src/coding_agent/plugins/kb.py` builds context packs from KB search results. Current provenance includes source kind/id, repo path, line range, chunk id, score, and evidence labels. Observability spans record count-style retrieval and context-pack attributes without raw query or content.

Future Topic integration can add topic metadata to context pack items through JSON-safe metadata and/or evidence fields, while preserving the existing rendered-content safety rules.

## Memory Evidence Provenance

`src/coding_agent/plugins/memory.py` keeps:

- long-term memories
- working memories
- topic file tags used for scoped recall

The memory plugin still reacts to explicit `topic_end` session events and can compact working memory into an evidence-backed long-term memory. This event handling is topic-adjacent plugin behavior, not the durable Topic model itself, and there is no longer a default file-overlap plugin that emits those events automatically.

Memory evidence is normalized into dictionaries with:

- `kind`
- `source_id`
- `label`
- optional repo path, chunk id, test node id, command label, session id, tape entry id, and line range

Console memory summaries currently come from context pack memory items or run metadata keys such as `memory_evidence`, `memory_candidates`, and `memories`.

## Evaluation Provenance

Evaluation tests and fixtures live under `tests/coding_agent/evaluation/` with context-system golden cases such as `context-system-retrieval-context-pack.yaml`. Current evaluation coverage asserts deterministic retrieval/context-pack behavior and result status, but there is no topic-level evaluation provenance field yet.

The likely extension points are:

- context pack item metadata for source topic ids and source entry ranges
- run metadata for topic-aware evaluation case provenance
- future Topic store links from eval artifacts back to topic ranges

## Developer Console

The Developer Console is server-rendered HTML in `src/coding_agent/ui/developer_console.py` and routed from `src/coding_agent/ui/http_server.py`.

Current routes that can be extended:

- `/console`
- `/console/sessions`
- `/console/runs`
- `/console/runs/{run_id}`
- `/console/interactions`
- `/console/tape`
- `/console/context`
- `/console/memory`
- `/console/actions`
- `/console/observability`
- `/console/workspaces`
- `/console/release`

The tape page already supports `tape_id`, `kind`, `run_id`, `tool_call_id`, and `anchor_type` query parameters. It renders entry summaries with payload/meta key names rather than raw payload content.

The context, memory, actions, and observability pages derive safe summaries from `AgentRunRecord.metadata`, tape debug results, or sanitized configuration. Topic list/detail pages can follow the same pattern:

- topic list by session, tape, and status
- topic detail as a tape range summary
- anchors and recall links
- linked runs/actions/validations when metadata is available
- no raw prompt/content/message/result/secret/text/command output/stdout/stderr/env rendering

## Observability

`src/coding_agent/observability.py` owns Coding Agent backend wiring and Prometheus metrics. AgentKit Core depends only on generic observation abstractions.

Current safety controls include:

- sensitive tracing attribute filtering for names containing content, env, message, output, prompt, result, secret, stderr, stdout, or text
- forbidden Prometheus labels such as run_id, session_id, trace_id, event_id, interaction_id, tool_call_id, file_path, prompt, message, content, command_output, and secret
- an allowlist of low-cardinality Prometheus labels
- known span/event names for runtime, retrieval, context, action, evaluation, validation, and storage metrics

Topic observability should use low-cardinality labels such as topic kind/status/profile where needed. `topic_id` may be safe in durable records, console routes, and trace correlation attributes if filtered through no-leak rules, but it must not be a Prometheus label.

`src/coding_agent/plugins/metrics.py` also listens to `topic_start` and `topic_end` session events for plugin-local topic metrics. This is separate from Prometheus metrics and should be reviewed when adding durable topic cost aggregates.

## Workspace Integration

Workspace Provider work added workspace metadata, console views, and dogfood/demo tests. Workspace records are distinct from sessions/runs and should not be conflated with topics.

Future topic views can optionally show workspace references by joining through run/session metadata, but Topic implementation should not require Docker or a hosted workspace provider.

## Files To Modify Later

Likely production files for G79-G83:

- `src/coding_agent/topic_store.py` or equivalent product-layer module for durable Topic records.
- `src/coding_agent/topic_lifecycle.py` for durable Topic lifecycle anchors.
- `src/coding_agent/ui/session_manager.py` for app-owned wiring and safe methods that create/finalize/abort/list topics.
- `src/coding_agent/ui/http_server.py` for Topic API/console routes and safe summaries.
- `src/coding_agent/ui/developer_console.py` for Topic dataclasses and renderers.
- `src/coding_agent/context_pack.py` if source topic ids or source entry ranges become first-class context pack metadata.
- `src/coding_agent/plugins/kb.py` for optional recalled topic summary evidence in context building.
- `src/coding_agent/plugins/memory.py` for topic provenance in memory evidence, without changing memory from reference-only behavior.
- `src/coding_agent/observability.py` for low-cardinality topic metrics and safe correlation attributes.
- `src/agentkit/tape/models.py` only if the ADR decides the public anchor names must become canonical in AgentKit. Prefer avoiding this unless compatibility requires it.
- `src/agentkit/storage/protocols.py` and `src/agentkit/storage/pg.py` only if generic optional tape debug query support needs a low-level extension. Prefer Coding Agent product-layer stores for topic data.

Likely test files for G79-G84:

- `tests/coding_agent/test_topic_store.py`
- `tests/coding_agent/test_topic_lifecycle.py`
- `tests/coding_agent/test_topic_context.py`
- `tests/coding_agent/test_topic_observability.py`
- `tests/ui/test_developer_console.py`
- targeted existing tape, context, memory, observability, and smoke tests listed below

## Tests To Preserve

Topic work should preserve these checks where relevant:

- `uv run pytest tests/agentkit/tape/ -v`
- `uv run pytest tests/integration/test_durable_runtime_smoke.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/coding_agent/evaluation/ -v`
- `uv run pytest tests/ui/test_developer_console.py -v`
- `uv run pytest tests/coding_agent/test_observability_platform_smoke.py -v`
- `uv run pytest tests/dogfood/test_local_dogfood_run.py -v`
- `uv run pytest tests/dogfood/test_workspace_provider_demo.py -v`
- `git diff --check -- .`

## G77 Conclusion

The current system is ready for a product-layer Topic implementation:

- Use existing tape anchors and tape search as the range boundary substrate.
- Add durable topic records in Coding Agent, not AgentKit Core.
- Keep Topic separate from Session and Run.
- Add context/memory/eval provenance as additive metadata.
- Extend Developer Console with safe summaries.
- Extend observability only with low-cardinality metrics and no-leak trace attributes.
