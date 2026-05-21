# Scheduled Runs Current State

G85 inspected the current Coding Agent runtime, Topic, action safety, workspace, Developer Console, and observability surfaces before adding topic-aware scheduled runs and proactive signals.

## Summary

- There is no first-class schedule, trigger, or proactive signal store in the current production code.
- Durable runs already exist and are the correct execution unit for scheduled/proactive work.
- Topics already exist as product-level durable records over tape ranges, with lifecycle anchors, recall links, cost aggregates, console pages, and safe observability rules.
- Scheduled/proactive work should create or continue a Topic, then create a normal durable run through existing app/runtime paths.
- Scheduled/proactive work must not call tools directly, bypass HITL, bypass approval policy, bypass command policy, bypass workspace policy, or create unbounded autonomous loops.
- AgentKit Core remains generic. Schedule and proactive signal ownership belongs in `coding_agent`.

## Existing Runtime Execution Surface

`src/coding_agent/ui/session_manager.py` is the app-layer owner for HTTP sessions and durable run creation.

Important existing functions and classes:

- `SessionManager.create_session(...)` creates a session with an approval policy, provider configuration, and execution binding.
- `SessionManager._run_metadata_for_session(...)` builds safe durable run metadata.
- `SessionManager._create_runtime_agent_run(...)` creates a queued `AgentRunRecord`.
- `SessionManager._update_runtime_agent_run(...)` updates run status, result, error, and metadata.
- `SessionManager._finish_runtime_agent_run(...)` finalizes completed, failed, or interrupted runtime runs.
- `SessionManager._build_session_runtime(...)` restores the stable tape, resolves workspace execution binding, creates the normal runtime pipeline, and maps approval policy to runtime configuration.
- `SessionManager._make_session_consumer(...)`, approval interaction helpers, and `submit_approval_response(...)` preserve HITL behavior.

`src/coding_agent/runtime_store.py` defines durable execution records:

- `AgentRunRecord` keyed by `run_id`, with `session_id`, `tape_id`, status, timestamps, metadata, result, and error.
- `RuntimeEventRecord` keyed by `event_id` and ordered by `run_id`/sequence.
- `RunMessageSnapshotRecord` keyed by `run_id`.
- `AgentInteractionRecord` for HITL/approval interactions.
- `PGRuntimeStore` schema tables: `agent_runs`, `runtime_events`, `run_message_snapshots`, and `agent_interactions`.

G85 found no existing scheduler loop that consumes queued work. ADR-0032 explicitly treats `queued` as a persisted run transition, not as a scheduler. Later goals should add bounded schedule planning in `coding_agent`, then use normal durable run creation rather than changing runtime semantics.

## Existing Topic Surface

`src/coding_agent/topic_store.py` defines durable Topic records and schema:

- `topics`
- `topic_anchors`
- `topic_recall_links`
- `topic_costs`

Important APIs:

- `PGTopicStore.create_topic(...)`
- `finalize_topic(...)`
- `abort_topic(...)`
- `load_topic(...)`
- `list_topics(...)`
- `find_open_topic(...)`
- `record_topic_anchor(...)`
- `record_recall_link(...)`
- `update_topic_cost(...)`
- `load_topic_cost(...)`

`src/coding_agent/topic_lifecycle.py` binds Topic records to tape anchors:

- `TopicLifecycle.create_topic(...)` writes a `topic_initial` product anchor encoded as `topic_start`.
- `finalize_topic(...)` writes a `topic_finalized` product anchor encoded as `topic_end`.
- `abort_topic(...)` writes a `topic_aborted` product anchor encoded as `topic_end`.
- `topic_range_entries(...)` lists tape entries within the topic range.
- `find_topic_anchors(...)` discovers product topic anchors on a tape.

`src/coding_agent/topic_recall.py`, `src/coding_agent/context_pack.py`, and `src/coding_agent/topic_provenance.py` already allow topic summaries, recall links, context metadata, memory provenance, eval provenance, and cost aggregation. Later scheduled/proactive work should reference `topic_id` in durable product records and safe trace correlation attributes, but must not use `topic_id` as a Prometheus label.

## Existing Tape And Debug Surface

AgentKit tape remains generic:

- `agentkit.tape.Tape` is an ordered list of entries.
- `agentkit.tape.anchor.Anchor` supports generic anchor types.
- Existing public product terminology maps `topic_initial` and `topic_finalized` to generic `topic_start` and `topic_end` anchors.
- `agentkit.storage.protocols.TapeDebugStore` supports `info(...)` and `search(...)`.
- `agentkit.storage.pg.PGTapeStore` can search by tape id, entry kind, run id, tool call id, and anchor type.

Scheduled/proactive features should not add new AgentKit Core primitives. Any schedule marker or proactive signal should be a Coding Agent durable record. Tape anchors should remain topic lifecycle or recall anchors, not high-frequency trigger logs.

## Existing Safety And Workspace Surface

Action safety is already product-owned in `src/coding_agent/action_safety/`:

- `command_policy.evaluate_command_policy(...)` classifies command execution with allow, deny, or approval-required decisions.
- `approval_routing.py` maps command/file/patch decisions to allow, deny, or approval-required routes.
- `safe_edit.py`, `patch_plan.py`, `validation_runner.py`, `validation_feedback.py`, and `workspace_snapshot.py` cover edit, patch, validation, feedback, and local snapshot behavior.
- Existing smoke coverage lives in `tests/coding_agent/action_safety/test_safe_action_smoke.py`.

Workspace providers are product-owned:

- `src/coding_agent/environment/workspace_provider.py`
- `src/coding_agent/environment/docker_workspace_provider.py`
- `src/coding_agent/ui/workspace_store.py`
- console route `/console/workspaces`

Scheduled/proactive work must route through `SessionManager` and existing session execution bindings so workspace policy remains in force. It must not instantiate tools or execute shell/file actions outside the normal runtime.

## Existing HTTP And Console Surface

`src/coding_agent/ui/http_server.py` currently exposes Developer Console pages for:

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
- `/console/topics`
- `/console/topics/{topic_id}`
- `/console/workspaces`
- `/console/release`

`src/coding_agent/ui/developer_console.py` renders server-side HTML with safe display helpers such as `safe_id_value`, `safe_label_value`, `safe_text_value`, and `safe_error_summary`. Existing tests in `tests/ui/test_developer_console.py` assert route availability, navigation, fixture rendering, and no raw prompt/message/command output leakage.

Later goals can add `/console/schedules` and `/console/proactive-signals` or a combined schedule/proactive page. Those pages should display schedule ids and topic ids only as route/product identifiers, never as Prometheus labels.

## Existing Observability Surface

`src/coding_agent/observability.py` owns Coding Agent backend wiring and Prometheus metrics.

Important current behavior:

- AgentKit Core only depends on generic observation abstractions.
- Langfuse/OTLP tracing and Prometheus metrics are additive through observation sinks.
- Sensitive trace attributes are filtered by key parts such as prompt, message, content, result, secret, text, stdout, stderr, output, and env.
- Prometheus labels are allowlisted and high-cardinality labels are rejected.
- Existing forbidden Prometheus labels include `run_id`, `session_id`, `trace_id`, `topic_id`, `event_id`, `interaction_id`, `tool_call_id`, `file_path`, prompt/message/content fields, command output, and secret.
- Topic metrics already use low-cardinality labels such as `topic_kind`, `topic_status`, and `topic_profile`.

G85 identified that later schedule/proactive metrics should add only low-cardinality labels such as schedule kind/status, trigger kind, signal kind, and policy route. `schedule_id` must be added to the forbidden-label set before metrics are introduced.

## Existing Tests To Preserve

Relevant regression tests:

- `uv run pytest tests/integration/test_durable_runtime_smoke.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/coding_agent/evaluation/ -v`
- `uv run pytest tests/ui/test_developer_console.py -v`
- `uv run pytest tests/coding_agent/test_topic_layer_smoke.py -v`
- `uv run pytest tests/coding_agent/test_observability_platform_smoke.py -v`
- `uv run pytest tests/coding_agent/test_topic_store.py tests/coding_agent/test_topic_lifecycle.py tests/coding_agent/test_topic_recall.py tests/coding_agent/test_topic_provenance.py -v`
- `git diff --check -- .`

Later schedule/proactive tests should use fake clocks, fake trigger engines, fake providers, temporary workspaces, and local/fake stores.

## Recommended G86-G92 Direction

Because the user supplied G85-G92 as a phase without detailed per-goal text, G85 will use this bounded split unless later instructions override it:

- G86: ADR for topic-aware scheduled runs and proactive signal boundaries.
- G87: Durable schedule/proactive signal schema and store.
- G88: Deterministic bounded schedule planner and trigger evaluation.
- G89: Topic-aware run launch intent that creates or continues Topics, then creates normal durable runs without bypassing safety.
- G90: Proactive signal ingestion, deduplication, cooldown, and bounded planning.
- G91: Developer Console and observability integration.
- G92: E2E smoke tests, usage docs, and implementation report.

## Exact Files Likely To Change Later

Likely new files:

- `src/coding_agent/scheduled_runs.py`
- `tests/coding_agent/test_scheduled_runs.py`
- `docs/adr/0040-topic-aware-scheduled-runs.md`
- `docs/scheduled_runs/USAGE.md`
- `docs/scheduled_runs/IMPLEMENTATION_REPORT.md`

Likely existing files:

- `src/coding_agent/ui/session_manager.py`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/ui/developer_console.py`
- `src/coding_agent/observability.py`
- `docs/release_hardening/release-verification.yaml`
- `tests/ui/test_developer_console.py`
- `tests/coding_agent/test_observability.py`
- `tests/coding_agent/test_topic_layer_smoke.py`

AgentKit Core files are not expected to change.

## Non-Goals For This Phase

- Bee workflow runtime, DAG nodes, `task.json`, or task manifest execution.
- External executor integrations.
- Desktop app or bridge.
- Multi-agent task graph.
- Homelab-specific templates.
- Production credentials or hosted services.
- Real external LLM calls in tests.
- Unbounded autonomous loops.
