"""Server-rendered Developer Console pages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from html import escape


FORBIDDEN_SENSITIVE_TOKENS = (
    "prompt",
    "message",
    "content",
    "command_output",
    "stdout",
    "stderr",
    "env",
    "secret",
    "result",
    "text",
)


@dataclass(frozen=True)
class ConsolePage:
    path: str
    title: str
    nav_label: str
    description: str


@dataclass(frozen=True)
class ConsoleSessionSummary:
    session_id: str
    status: str
    turn_status: str
    created_at: datetime
    updated_at: datetime
    current_turn_id: str | None = None


@dataclass(frozen=True)
class ConsoleRunSummary:
    run_id: str
    session_id: str
    status: str
    started_at: datetime
    ended_at: datetime | None = None
    error_summary: str | None = None


@dataclass(frozen=True)
class ConsoleSnapshotSummary:
    snapshot_id: str
    message_count: int
    created_at: datetime | None
    message_labels: tuple[str, ...]
    metadata_keys: tuple[str, ...]


@dataclass(frozen=True)
class ConsoleDisplayEventSummary:
    sequence: int | None
    source_event_id: str
    display_kind: str
    created_at: datetime
    payload_keys: tuple[str, ...]


@dataclass(frozen=True)
class ConsoleRunDetail:
    run_id: str
    session_id: str
    tape_id: str | None
    parent_run_id: str | None
    agent_id: str | None
    status: str
    started_at: datetime
    ended_at: datetime | None
    error_summary: str | None
    metadata_keys: tuple[str, ...]
    result_keys: tuple[str, ...]
    snapshot: ConsoleSnapshotSummary | None
    events: tuple[ConsoleDisplayEventSummary, ...]


@dataclass(frozen=True)
class ConsoleInteractionSummary:
    interaction_id: str
    run_id: str
    session_id: str
    tool_call_id: str | None
    interaction_kind: str
    status: str
    created_at: datetime
    resolved_at: datetime | None


@dataclass(frozen=True)
class ConsoleTapeInfo:
    tape_id: str
    entry_count: int
    first_seq: int
    last_seq: int


@dataclass(frozen=True)
class ConsoleTapeEntrySummary:
    tape_id: str
    seq: int
    kind: str
    run_id: str | None
    tool_call_id: str | None
    anchor_type: str | None
    payload_keys: tuple[str, ...]
    meta_keys: tuple[str, ...]


@dataclass(frozen=True)
class ConsoleContextEvidence:
    kind: str
    label: str
    source_id: str
    repo_path: str | None
    line_start: int | None
    line_end: int | None
    score: float | None
    reason: str | None


@dataclass(frozen=True)
class ConsoleContextSectionSummary:
    title: str
    items: tuple[ConsoleContextEvidence, ...]


@dataclass(frozen=True)
class ConsoleContextSummary:
    run_id: str | None
    sections: tuple[ConsoleContextSectionSummary, ...]


@dataclass(frozen=True)
class ConsoleMemoryEvidence:
    run_id: str | None
    source_id: str
    label: str
    status: str | None
    tags_count: int | None
    evidence_count: int | None
    repo_path: str | None
    line_start: int | None
    line_end: int | None


@dataclass(frozen=True)
class ConsoleMemorySummary:
    run_id: str | None
    items: tuple[ConsoleMemoryEvidence, ...]
    reviews: tuple["ConsoleMemoryReviewSummary", ...] = ()


@dataclass(frozen=True)
class ConsoleMemoryReviewSummary:
    source_id: str
    label: str
    kind: str
    status: str
    run_id: str | None
    topic_id: str | None
    task_id: str | None
    evidence_count: int | None
    source_range_count: int | None


@dataclass(frozen=True)
class ConsoleActionSummary:
    action_id: str | None
    run_id: str | None
    interaction_id: str | None
    validation_id: str | None
    kind: str
    status: str
    policy_decision: str | None
    risk_level: str | None
    changed_path_count: int | None
    extension_buckets: tuple[str, ...]
    approval_status: str | None
    patch_summary: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ConsoleValidationOutcomeSummary:
    label: str
    status: str
    exit_code: int | None
    duration_ms: int | None
    policy_decision: str | None
    failure_summary: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ConsoleActionValidationSummary:
    run_id: str | None
    actions: tuple[ConsoleActionSummary, ...]
    validation_status: str | None
    validations: tuple[ConsoleValidationOutcomeSummary, ...]


@dataclass(frozen=True)
class ConsoleCorrelationSummary:
    session_id: str | None
    run_id: str | None
    tape_id: str | None
    topic_id: str | None
    retrieval_id: str | None
    action_id: str | None
    validation_id: str | None
    interaction_id: str | None


@dataclass(frozen=True)
class ConsoleTopicSummary:
    topic_id: str
    tape_id: str | None
    session_id: str | None
    kind: str
    status: str
    title: str | None
    summary: str | None
    topic_initial_seq: int | None
    topic_finalized_seq: int | None
    run_count: int
    cost_total_tokens: int | None


@dataclass(frozen=True)
class ConsoleTopicAnchorSummary:
    seq: int | None
    anchor_type: str
    entry_id: str | None


@dataclass(frozen=True)
class ConsoleTopicRecallSummary:
    recalled_topic_id: str
    relation: str
    anchor_seq: int | None


@dataclass(frozen=True)
class ConsoleTopicCostSummary:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    run_count: int
    action_count: int
    validation_count: int
    tool_call_count: int


@dataclass(frozen=True)
class ConsoleTopicDetail:
    summary: ConsoleTopicSummary
    anchors: tuple[ConsoleTopicAnchorSummary, ...]
    recalls: tuple[ConsoleTopicRecallSummary, ...]
    cost: ConsoleTopicCostSummary | None
    runs: tuple[ConsoleRunSummary, ...]
    actions: tuple[ConsoleActionSummary, ...]
    validations: tuple[ConsoleValidationOutcomeSummary, ...]


@dataclass(frozen=True)
class ConsoleScheduleSummary:
    schedule_id: str
    session_id: str
    topic_id: str | None
    kind: str
    status: str
    cadence: str
    title: str | None
    next_due_at: datetime | None
    last_triggered_at: datetime | None


@dataclass(frozen=True)
class ConsoleScheduleTriggerSummary:
    trigger_id: str
    schedule_id: str
    signal_id: str | None
    topic_id: str | None
    run_id: str | None
    status: str
    due_at: datetime
    planned_at: datetime
    reason: str | None


@dataclass(frozen=True)
class ConsoleProactiveSignalSummary:
    signal_id: str
    session_id: str | None
    topic_id: str | None
    kind: str
    status: str
    observed_at: datetime
    cooldown_until: datetime | None
    summary: str | None


@dataclass(frozen=True)
class ConsoleSchedulesPage:
    schedules: tuple[ConsoleScheduleSummary, ...]
    triggers: tuple[ConsoleScheduleTriggerSummary, ...]
    signals: tuple[ConsoleProactiveSignalSummary, ...]


@dataclass(frozen=True)
class ConsoleBeeTaskSummary:
    task_id: str
    topic_id: str | None
    session_id: str | None
    kind: str
    profile: str
    status: str
    node_count: int
    run_count: int


@dataclass(frozen=True)
class ConsoleBeeNodeSummary:
    task_id: str
    node_id: str
    run_id: str | None
    topic_id: str | None
    session_id: str | None
    task_kind: str
    task_profile: str
    kind: str
    profile: str
    status: str
    context_profile: str | None
    validation_profile: str | None
    workspace_policy: str | None
    approval_policy: str | None
    action_policy: str | None
    workspace_binding: str | None


@dataclass(frozen=True)
class ConsoleBeeTemplateSummary:
    template_id: str
    kind: str
    profile: str
    title: str
    feature_count: int
    has_commands: bool
    command_count: int


@dataclass(frozen=True)
class ConsoleBeeRunArtifactSummary:
    task_id: str
    template_id: str
    topic_id: str
    status: str
    node_count: int
    run_count: int
    action_count: int
    validation_count: int
    executor_count: int
    has_report: bool
    has_memory_candidates: bool


@dataclass(frozen=True)
class ConsoleBeeCommandIntentSummary:
    template_id: str
    name: str
    profile: str
    policy: str
    category: str
    validation_label: str | None
    status: str
    bridge_status: str | None = None
    approval_route: str | None = None
    evidence_status: str | None = None


@dataclass(frozen=True)
class ConsoleBeeLaunchSummary:
    launch_id: str
    source: str
    status: str
    template_id: str | None
    task_id: str | None
    topic_id: str | None
    schedule_id: str | None = None
    signal_id: str | None = None
    error_summary: str | None = None


@dataclass(frozen=True)
class ConsoleBeePackSummary:
    pack_id: str
    name: str
    version: str
    source_type: str
    domain_profile: str | None
    tags: tuple[str, ...]
    template_count: int


@dataclass(frozen=True)
class ConsoleBeePackTemplateSummary:
    pack_id: str
    template_id: str
    source_type: str
    kind: str
    profile: str
    title: str


@dataclass(frozen=True)
class ConsoleBeePackCompatibilitySummary:
    pack_id: str | None
    source_type: str
    status: str
    check_count: int
    finding_count: int
    template_count: int
    recommended_fixes: tuple[str, ...]


@dataclass(frozen=True)
class ConsoleBeePackDryRunSummary:
    pack_id: str
    template_id: str
    source_type: str
    status: str
    task_json_path: str
    report_path: str
    evidence_dir: str
    memory_candidates_path: str
    node_count: int
    command_count: int
    warning_count: int


@dataclass(frozen=True)
class ConsoleExecutorRunSummary:
    executor_run_id: str
    executor_kind: str
    status: str
    task_id: str | None
    node_id: str | None
    launch_id: str | None
    topic_id: str | None
    capability_status: str | None = None
    sanitized_summary: str | None = None


@dataclass(frozen=True)
class ConsoleBeePage:
    tasks: tuple[ConsoleBeeTaskSummary, ...]
    nodes: tuple[ConsoleBeeNodeSummary, ...]
    packs: tuple[ConsoleBeePackSummary, ...] = ()
    pack_templates: tuple[ConsoleBeePackTemplateSummary, ...] = ()
    pack_compatibility: tuple[ConsoleBeePackCompatibilitySummary, ...] = ()
    pack_dry_runs: tuple[ConsoleBeePackDryRunSummary, ...] = ()
    templates: tuple[ConsoleBeeTemplateSummary, ...] = ()
    run_artifacts: tuple[ConsoleBeeRunArtifactSummary, ...] = ()
    commands: tuple[ConsoleBeeCommandIntentSummary, ...] = ()
    launches: tuple[ConsoleBeeLaunchSummary, ...] = ()
    executor_runs: tuple[ConsoleExecutorRunSummary, ...] = ()


@dataclass(frozen=True)
class ConsoleObservabilitySummary:
    correlation: ConsoleCorrelationSummary | None
    metrics_enabled: bool
    metrics_path: str
    tracing_backend: str | None
    metrics_backend: str | None
    langfuse_url: str | None
    grafana_url: str | None


@dataclass(frozen=True)
class ConsoleWorkspaceCapabilitySummary:
    provider: str
    available: bool
    reason: str
    supports_provision: bool
    supports_archive: bool
    supports_diff: bool
    supports_patch: bool
    supports_publish: bool


@dataclass(frozen=True)
class ConsoleWorkspaceSummary:
    workspace_id: str
    status: str
    updated_at: datetime
    session_id: str | None
    provider: str | None
    provider_instance_id: str | None
    workspace_host_label: str | None
    source_kind: str | None
    retention_policy: str | None
    expires_at: datetime | None
    is_local: bool | None
    result_ref_keys: tuple[str, ...]
    cleanup_error: str | None


@dataclass(frozen=True)
class ConsoleReleaseGateSummary:
    gate_id: str
    command: str
    required: bool
    scope: str


@dataclass(frozen=True)
class ConsoleReleaseSummary:
    health_status: str
    session_count: int
    version: str
    readiness_status: str
    readiness_checks: tuple[tuple[str, str], ...]
    release_manifest_name: str | None
    release_gates: tuple[ConsoleReleaseGateSummary, ...]


CONSOLE_PAGES: tuple[ConsolePage, ...] = (
    ConsolePage(
        path="/console/sessions",
        title="Sessions",
        nav_label="Sessions",
        description="Recent session summaries will appear here.",
    ),
    ConsolePage(
        path="/console/runs",
        title="Runs",
        nav_label="Runs",
        description="Durable runtime runs will appear here.",
    ),
    ConsolePage(
        path="/console/interactions",
        title="HITL / Interactions",
        nav_label="HITL / Interactions",
        description="Pending and resolved approval interactions will appear here.",
    ),
    ConsolePage(
        path="/console/tape",
        title="Tape",
        nav_label="Tape",
        description="Tape info and search results will appear here.",
    ),
    ConsolePage(
        path="/console/context",
        title="Context",
        nav_label="Context",
        description="Retrieval hits and context-pack evidence will appear here.",
    ),
    ConsolePage(
        path="/console/memory",
        title="Memory",
        nav_label="Memory",
        description="Memory evidence and candidates will appear here.",
    ),
    ConsolePage(
        path="/console/actions",
        title="Actions / Validation",
        nav_label="Actions / Validation",
        description="Action, policy, and validation summaries will appear here.",
    ),
    ConsolePage(
        path="/console/observability",
        title="Observability",
        nav_label="Observability",
        description="Metrics, trace, and dashboard links will appear here.",
    ),
    ConsolePage(
        path="/console/topics",
        title="Topics",
        nav_label="Topics",
        description="Topic ranges, recalls, costs, and related run metadata will appear here.",
    ),
    ConsolePage(
        path="/console/schedules",
        title="Schedules",
        nav_label="Schedules",
        description="Topic-aware scheduled runs and proactive signals will appear here.",
    ),
    ConsolePage(
        path="/console/bee",
        title="Bee Tasks",
        nav_label="Bee Tasks",
        description="Bee task manifests, node launch references, and policy bindings will appear here.",
    ),
    ConsolePage(
        path="/console/workspaces",
        title="Workspaces",
        nav_label="Workspaces",
        description="Workspace provider inventory and local capability status will appear here.",
    ),
    ConsolePage(
        path="/console/release",
        title="Release / Health",
        nav_label="Release / Health",
        description="Health and release verification information will appear here.",
    ),
)

_PAGE_BY_PATH = {page.path: page for page in CONSOLE_PAGES}
_ROOT_PAGE = ConsolePage(
    path="/console",
    title="Console Overview",
    nav_label="Overview",
    description="Developer Console overview for runtime, context, action, and observability debugging.",
)


def render_console_page(path: str) -> str:
    page = _ROOT_PAGE if path == "/console" else _PAGE_BY_PATH[path]
    return _html_document(
        title=page.title,
        body=(
            f"<h1>{escape(page.title)}</h1>"
            f'<p class="lede">{escape(page.description)}</p>'
            '<section class="empty-state">'
            "<h2>No data loaded yet.</h2>"
            "<p>This page is wired for the Developer Console shell. "
            "Goal-specific data views are added in later console goals.</p>"
            "</section>"
        ),
        active_path=path,
    )


def render_console_sessions_page(sessions: list[ConsoleSessionSummary]) -> str:
    page = _PAGE_BY_PATH["/console/sessions"]
    body = (
        f"<h1>{escape(page.title)}</h1>"
        f'<p class="lede">{escape(page.description)}</p>' + _session_table(sessions)
    )
    return _html_document(title=page.title, body=body, active_path=page.path)


def render_console_runs_page(
    runs: list[ConsoleRunSummary],
    *,
    status_filter: str | None = None,
) -> str:
    page = _PAGE_BY_PATH["/console/runs"]
    filter_note = (
        '<p class="filter-note">Status filter active.</p>' if status_filter else ""
    )
    body = (
        f"<h1>{escape(page.title)}</h1>"
        f'<p class="lede">{escape(page.description)}</p>'
        f"{filter_note}" + _run_table(runs)
    )
    return _html_document(title=page.title, body=body, active_path=page.path)


def render_console_run_detail_page(detail: ConsoleRunDetail) -> str:
    title = "Run Detail"
    body = (
        f"<h1>{title}</h1>"
        '<p class="lede">Replayable runtime summary with sanitized snapshot and event metadata.</p>'
        f"{_run_metadata_detail(detail)}"
        f"{_run_snapshot_section(detail.snapshot)}"
        f"{_run_events_section(detail.run_id, detail.events)}"
        f"{_run_detail_links(detail)}"
    )
    return _html_document(title=title, body=body, active_path="/console/runs")


def render_console_interactions_page(
    interactions: list[ConsoleInteractionSummary],
) -> str:
    page = _PAGE_BY_PATH["/console/interactions"]
    pending = [
        interaction
        for interaction in interactions
        if interaction.resolved_at is None and interaction.status == "pending"
    ]
    resolved = [
        interaction for interaction in interactions if interaction not in pending
    ]
    body = (
        f"<h1>{escape(page.title)}</h1>"
        f'<p class="lede">{escape(page.description)}</p>'
        f"{_interaction_table('Pending Interactions', pending)}"
        f"{_interaction_table('Resolved Interactions', resolved)}"
    )
    return _html_document(title=page.title, body=body, active_path=page.path)


def render_console_tape_page(
    info: ConsoleTapeInfo | None,
    entries: list[ConsoleTapeEntrySummary],
) -> str:
    page = _PAGE_BY_PATH["/console/tape"]
    body = (
        f"<h1>{escape(page.title)}</h1>"
        f'<p class="lede">{escape(page.description)}</p>'
        f"{_tape_info_section(info)}"
        f"{_tape_search_section(entries)}"
    )
    return _html_document(title=page.title, body=body, active_path=page.path)


def render_console_context_page(context: ConsoleContextSummary | None) -> str:
    page = _PAGE_BY_PATH["/console/context"]
    body = (
        f"<h1>{escape(page.title)}</h1>"
        f'<p class="lede">{escape(page.description)}</p>'
        "<p>Context Inspector</p>"
        f"{_context_sections(context)}"
    )
    return _html_document(title=page.title, body=body, active_path=page.path)


def render_console_memory_page(memory: ConsoleMemorySummary | None) -> str:
    page = _PAGE_BY_PATH["/console/memory"]
    body = (
        f"<h1>{escape(page.title)}</h1>"
        f'<p class="lede">{escape(page.description)}</p>'
        "<p>Read-only memory evidence from existing run metadata and context packs.</p>"
        f"{_memory_review_section(memory)}"
        f"{_memory_evidence_section(memory)}"
    )
    return _html_document(title=page.title, body=body, active_path=page.path)


def render_console_actions_page(
    summary: ConsoleActionValidationSummary | None,
) -> str:
    page = _PAGE_BY_PATH["/console/actions"]
    body = (
        f"<h1>{escape(page.title)}</h1>"
        f'<p class="lede">{escape(page.description)}</p>'
        f"{_action_summary_section(summary)}"
        f"{_validation_summary_section(summary)}"
    )
    return _html_document(title=page.title, body=body, active_path=page.path)


def render_console_observability_page(
    summary: ConsoleObservabilitySummary,
) -> str:
    page = _PAGE_BY_PATH["/console/observability"]
    body = (
        f"<h1>{escape(page.title)}</h1>"
        f'<p class="lede">{escape(page.description)}</p>'
        f"{_correlation_section(summary.correlation)}"
        f"{_observability_backend_section(summary)}"
    )
    return _html_document(title=page.title, body=body, active_path=page.path)


def render_console_topics_page(topics: list[ConsoleTopicSummary]) -> str:
    page = _PAGE_BY_PATH["/console/topics"]
    body = (
        f"<h1>{escape(page.title)}</h1>"
        f'<p class="lede">{escape(page.description)}</p>'
        f"{_topic_table(topics)}"
    )
    return _html_document(title=page.title, body=body, active_path=page.path)


def render_console_topic_detail_page(detail: ConsoleTopicDetail) -> str:
    title = "Topic Detail"
    body = (
        f"<h1>{title}</h1>"
        '<p class="lede">Tape range and provenance summary for a single topic.</p>'
        f"{_topic_detail_metadata(detail.summary)}"
        f"{_topic_anchor_table(detail.anchors)}"
        f"{_topic_recall_table(detail.recalls)}"
        f"{_topic_cost_section(detail.cost)}"
        f"{_topic_related_runs_section(detail.runs)}"
        f"{_topic_related_actions_section(detail.actions)}"
        f"{_topic_related_validations_section(detail.validations, run_id=_first_run_id(detail.runs))}"
    )
    return _html_document(title=title, body=body, active_path="/console/topics")


def render_console_schedules_page(page_summary: ConsoleSchedulesPage) -> str:
    page = _PAGE_BY_PATH["/console/schedules"]
    body = (
        f"<h1>{escape(page.title)}</h1>"
        f'<p class="lede">{escape(page.description)}</p>'
        f"{_schedule_table(page_summary.schedules)}"
        f"{_schedule_trigger_table(page_summary.triggers)}"
        f"{_proactive_signal_table(page_summary.signals)}"
    )
    return _html_document(title=page.title, body=body, active_path=page.path)


def render_console_bee_page(page_summary: ConsoleBeePage) -> str:
    page = _PAGE_BY_PATH["/console/bee"]
    body = (
        f"<h1>{escape(page.title)}</h1>"
        f'<p class="lede">{escape(page.description)}</p>'
        f"{_bee_task_table(page_summary.tasks)}"
        f"{_bee_node_table(page_summary.nodes)}"
        f"{_bee_pack_table(page_summary.packs)}"
        f"{_bee_pack_template_table(page_summary.pack_templates)}"
        f"{_bee_pack_compatibility_table(page_summary.pack_compatibility)}"
        f"{_bee_pack_dry_run_table(page_summary.pack_dry_runs)}"
        f"{_bee_template_table(page_summary.templates)}"
        f"{_bee_run_artifact_table(page_summary.run_artifacts)}"
        f"{_bee_command_intent_table(page_summary.commands)}"
        f"{_bee_launch_table(page_summary.launches)}"
        f"{_executor_run_table(page_summary.executor_runs)}"
    )
    return _html_document(title=page.title, body=body, active_path=page.path)


def render_console_workspaces_page(
    workspaces: list[ConsoleWorkspaceSummary],
    capability: ConsoleWorkspaceCapabilitySummary | None,
) -> str:
    page = _PAGE_BY_PATH["/console/workspaces"]
    body = (
        f"<h1>{escape(page.title)}</h1>"
        f'<p class="lede">{escape(page.description)}</p>'
        f"{_workspace_capability_section(capability)}"
        f"{_workspace_table(workspaces)}"
    )
    return _html_document(title=page.title, body=body, active_path=page.path)


def render_console_release_page(summary: ConsoleReleaseSummary) -> str:
    page = _PAGE_BY_PATH["/console/release"]
    body = (
        f"<h1>{escape(page.title)}</h1>"
        f'<p class="lede">{escape(page.description)}</p>'
        f"{_health_section(summary)}"
        f"{_release_gate_section(summary)}"
    )
    return _html_document(title=page.title, body=body, active_path=page.path)


def safe_error_summary(error: str | None) -> str | None:
    if error is None:
        return None
    normalized = error.lower()
    if any(token in normalized for token in FORBIDDEN_SENSITIVE_TOKENS):
        return "Sensitive error summary redacted."
    return error[:240]


def safe_id_value(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if _contains_sensitive_token(value):
        return "redacted"
    return value[:120]


def safe_text_value(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if _contains_sensitive_token(value):
        return "redacted"
    return value[:240]


_SAFE_LABEL_RE = re.compile(r"[A-Za-z0-9_.:-]{1,80}")


def safe_label_value(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if _contains_sensitive_token(value):
        return "redacted"
    if _SAFE_LABEL_RE.fullmatch(value) is None:
        return "redacted"
    return value


def safe_key_tuple(mapping: dict[str, object]) -> tuple[str, ...]:
    return tuple(
        sorted(key for key in mapping if not _contains_sensitive_token(str(key)))
    )


def message_label(message: dict[str, object]) -> str:
    role = message.get("role")
    if isinstance(role, str) and role in {"user", "assistant", "system", "tool"}:
        return f"role:{role}"
    message_type = message.get("type") or message.get("message_type")
    if isinstance(message_type, str) and message_type in {
        "user",
        "assistant",
        "system",
        "tool",
    }:
        return f"type:{message_type}"
    return "message"


def _contains_sensitive_token(value: str) -> bool:
    normalized = value.lower()
    return any(token in normalized for token in FORBIDDEN_SENSITIVE_TOKENS)


def _html_document(*, title: str, body: str, active_path: str) -> str:
    return (
        "<!doctype html>"
        '<html lang="en">'
        "<head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)} - Developer Console</title>"
        "<style>"
        ":root{color-scheme:light dark;font-family:Inter,ui-sans-serif,system-ui,sans-serif;}"
        "body{margin:0;background:#f7f7f4;color:#1f2933;}"
        "a{color:#14532d;text-decoration:none;}"
        "a:hover{text-decoration:underline;}"
        ".layout{display:grid;grid-template-columns:260px minmax(0,1fr);min-height:100vh;}"
        "nav{background:#17324d;color:#f8fafc;padding:24px 18px;}"
        "nav h2{font-size:18px;margin:0 0 18px;}"
        "nav a{display:block;color:#e8eef5;padding:9px 10px;border-radius:6px;margin:2px 0;}"
        "nav a.active{background:#f8fafc;color:#17324d;font-weight:700;}"
        "main{padding:32px;}"
        "h1{font-size:28px;line-height:1.2;margin:0 0 10px;}"
        ".lede{max-width:840px;margin:0 0 24px;color:#52616f;}"
        ".empty-state{max-width:840px;border:1px solid #cbd5df;background:#fff;padding:20px;border-radius:8px;}"
        ".empty-state h2{font-size:18px;margin:0 0 8px;}"
        ".empty-state p{margin:0;color:#52616f;}"
        "table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #cbd5df;}"
        "th,td{text-align:left;border-bottom:1px solid #e5e7eb;padding:10px;vertical-align:top;}"
        "th{font-size:13px;text-transform:uppercase;color:#52616f;background:#f1f5f9;}"
        ".filter-note{font-size:14px;color:#52616f;}"
        ".status{font-weight:700;}"
        "dl{display:grid;grid-template-columns:180px minmax(0,1fr);gap:8px 16px;background:#fff;border:1px solid #cbd5df;padding:16px;}"
        "dt{font-weight:700;color:#52616f;}dd{margin:0;}"
        "section{margin:0 0 24px;}"
        ".pill-list{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 0;padding:0;list-style:none;}"
        ".pill-list li{border:1px solid #cbd5df;border-radius:6px;padding:4px 8px;background:#fff;}"
        "@media(max-width:760px){.layout{grid-template-columns:1fr;}nav{position:static;}main{padding:20px;}}"
        "</style>"
        "</head>"
        "<body>"
        '<div class="layout">'
        f"{_navigation(active_path)}"
        f"<main>{body}</main>"
        "</div>"
        "</body>"
        "</html>"
    )


def _run_metadata_detail(detail: ConsoleRunDetail) -> str:
    return (
        '<section aria-label="Run metadata">'
        "<h2>Run Metadata</h2>"
        "<dl>"
        f"<dt>Run ID</dt><dd>{escape(detail.run_id)}</dd>"
        f"<dt>Session ID</dt><dd>{escape(detail.session_id)}</dd>"
        f"<dt>Status</dt><dd>{escape(detail.status)}</dd>"
        f"<dt>Started</dt><dd>{escape(_format_datetime(detail.started_at))}</dd>"
        f"<dt>Finished</dt><dd>{escape(_format_optional_datetime(detail.ended_at))}</dd>"
        f"<dt>Tape ID</dt><dd>{escape(detail.tape_id or '-')}</dd>"
        f"<dt>Parent Run ID</dt><dd>{escape(detail.parent_run_id or '-')}</dd>"
        f"<dt>Agent ID</dt><dd>{escape(detail.agent_id or '-')}</dd>"
        f"<dt>Error Summary</dt><dd>{escape(detail.error_summary or '-')}</dd>"
        f"<dt>Metadata Keys</dt><dd>{escape(_join_or_dash(detail.metadata_keys))}</dd>"
        f"<dt>Result Keys</dt><dd>{escape(_join_or_dash(detail.result_keys))}</dd>"
        "</dl>"
        "</section>"
    )


def _interaction_table(
    title: str,
    interactions: list[ConsoleInteractionSummary],
) -> str:
    if not interactions:
        return _empty_state(title, "No data loaded yet. No interactions are available.")
    rows = []
    for interaction in interactions:
        rows.append(
            "<tr>"
            f"<td>{escape(interaction.interaction_id)}</td>"
            f'<td><a href="/console/runs/{escape(interaction.run_id)}">'
            f"{escape(interaction.run_id)}</a></td>"
            f"<td>{escape(interaction.session_id)}</td>"
            f"<td>{escape(interaction.tool_call_id or '-')}</td>"
            f"<td>{escape(interaction.interaction_kind)}</td>"
            f'<td class="status">{escape(interaction.status)}</td>'
            f"<td>{escape(_format_datetime(interaction.created_at))}</td>"
            f"<td>{escape(_format_optional_datetime(interaction.resolved_at))}</td>"
            "</tr>"
        )
    return (
        f'<section aria-label="{escape(title)}">'
        f"<h2>{escape(title)}</h2>"
        '<table aria-label="Developer Console interactions">'
        "<thead><tr>"
        "<th>Interaction ID</th><th>Run ID</th><th>Session ID</th>"
        "<th>Tool Call ID</th><th>Kind</th><th>Status</th>"
        "<th>Created</th><th>Resolved</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _tape_info_section(info: ConsoleTapeInfo | None) -> str:
    if info is None:
        return _empty_state("Tape Info", "No tape info is available.")
    return (
        '<section aria-label="Tape Info">'
        "<h2>Tape Info</h2>"
        "<dl>"
        f"<dt>Tape ID</dt><dd>{escape(info.tape_id)}</dd>"
        f"<dt>Entry Count</dt><dd>{info.entry_count}</dd>"
        f"<dt>First Seq</dt><dd>{info.first_seq}</dd>"
        f"<dt>Last Seq</dt><dd>{info.last_seq}</dd>"
        "</dl>"
        "</section>"
    )


def _tape_search_section(entries: list[ConsoleTapeEntrySummary]) -> str:
    if not entries:
        return _empty_state("Tape Search", "No data loaded yet. No tape entries match.")
    rows = []
    for entry in entries:
        rows.append(
            "<tr>"
            f"<td>{escape(entry.tape_id)}</td>"
            f"<td>{entry.seq}</td>"
            f"<td>{escape(entry.kind)}</td>"
            f"<td>{escape(entry.run_id or '-')}</td>"
            f"<td>{escape(entry.tool_call_id or '-')}</td>"
            f"<td>{escape(entry.anchor_type or '-')}</td>"
            f"<td>{escape(_join_or_dash(entry.payload_keys))}</td>"
            f"<td>{escape(_join_or_dash(entry.meta_keys))}</td>"
            "</tr>"
        )
    return (
        '<section aria-label="Tape Search">'
        "<h2>Tape Search</h2>"
        '<table aria-label="Tape search results">'
        "<thead><tr><th>Tape ID</th><th>Seq</th><th>Kind</th>"
        "<th>Run ID</th><th>Tool Call ID</th><th>Anchor Type</th>"
        "<th>Payload Keys</th><th>Meta Keys</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _context_sections(context: ConsoleContextSummary | None) -> str:
    if context is None or not context.sections:
        return _empty_state(
            "Context Inspector",
            "No data loaded yet. No context pack evidence is available.",
        )
    run_link = (
        f'<p><a href="/console/runs/{escape(context.run_id)}">Run detail</a></p>'
        if context.run_id
        else ""
    )
    sections = [run_link]
    for section in context.sections:
        rows = []
        for item in section.items:
            rows.append(
                "<tr>"
                f"<td>{escape(item.kind)}</td>"
                f"<td>{escape(item.label)}</td>"
                f"<td>{escape(item.source_id)}</td>"
                f"<td>{escape(item.repo_path or '-')}</td>"
                f"<td>{escape(_line_range(item.line_start, item.line_end))}</td>"
                f"<td>{escape('-' if item.score is None else str(item.score))}</td>"
                f"<td>{escape(item.reason or '-')}</td>"
                "</tr>"
            )
        sections.append(
            '<section aria-label="Context evidence">'
            f"<h2>{escape(section.title)}</h2>"
            '<table aria-label="Context evidence results">'
            "<thead><tr><th>Kind</th><th>Label</th><th>Source ID</th>"
            "<th>Source Path</th><th>Line Range</th><th>Score</th>"
            "<th>Evidence Reason</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody>"
            "</table>"
            "</section>"
        )
    return "".join(sections)


def _memory_evidence_section(memory: ConsoleMemorySummary | None) -> str:
    if memory is None or not memory.items:
        return _empty_state(
            "Memory Evidence",
            "No data loaded yet. No memory evidence is available.",
        )
    run_link = (
        f'<p><a href="/console/runs/{escape(memory.run_id)}">Run detail</a></p>'
        if memory.run_id
        else ""
    )
    rows = []
    for item in memory.items:
        rows.append(
            "<tr>"
            f"<td>{escape(item.source_id)}</td>"
            f"<td>{escape(item.label)}</td>"
            f"<td>{escape(item.status or '-')}</td>"
            f"<td>{escape('-' if item.tags_count is None else str(item.tags_count))}</td>"
            f"<td>{escape('-' if item.evidence_count is None else str(item.evidence_count))}</td>"
            f"<td>{escape(item.repo_path or '-')}</td>"
            f"<td>{escape(_line_range(item.line_start, item.line_end))}</td>"
            "</tr>"
        )
    return (
        '<section aria-label="Memory evidence">'
        "<h2>Memory Evidence</h2>"
        f"{run_link}"
        '<table aria-label="Memory evidence results">'
        "<thead><tr><th>Source ID</th><th>Label</th><th>Status</th>"
        "<th>Tags</th><th>Evidence</th><th>Source Path</th><th>Line Range</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _memory_review_section(memory: ConsoleMemorySummary | None) -> str:
    if memory is None or not memory.reviews:
        return _empty_state(
            "Memory Review Inbox",
            "No memory candidates or reviewed memories are available.",
        )
    rows = []
    for item in memory.reviews:
        topic = (
            f'<a href="/console/topics/{escape(item.topic_id)}">{escape(item.topic_id)}</a>'
            if item.topic_id
            else "-"
        )
        run = (
            f'<a href="/console/runs/{escape(item.run_id)}">{escape(item.run_id)}</a>'
            if item.run_id
            else "-"
        )
        rows.append(
            "<tr>"
            f"<td>{escape(item.source_id)}</td>"
            f"<td>{escape(item.label)}</td>"
            f"<td>{escape(item.kind)}</td>"
            f'<td class="status">{escape(item.status)}</td>'
            f"<td>{topic}</td>"
            f"<td>{escape(item.task_id or '-')}</td>"
            f"<td>{run}</td>"
            f"<td>{escape('-' if item.evidence_count is None else str(item.evidence_count))}</td>"
            f"<td>{escape('-' if item.source_range_count is None else str(item.source_range_count))}</td>"
            "</tr>"
        )
    return (
        '<section aria-label="Memory review inbox">'
        "<h2>Memory Review Inbox</h2>"
        '<p class="lede">Review actions are intentionally read-only in this console view; use the product API or review store to change candidate status.</p>'
        '<table aria-label="Memory candidate and review results">'
        "<thead><tr><th>Memory ID</th><th>Title</th><th>Kind</th>"
        "<th>Status</th><th>Topic</th><th>Task</th><th>Run</th>"
        "<th>Evidence</th><th>Source Ranges</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _action_summary_section(
    summary: ConsoleActionValidationSummary | None,
) -> str:
    if summary is None or not summary.actions:
        return _empty_state(
            "Action Executions",
            "No data loaded yet. No action summaries are available.",
        )
    rows = []
    for action in summary.actions:
        rows.append(
            "<tr>"
            f"<td>{escape(action.action_id or '-')}</td>"
            f"<td>{escape(action.kind)}</td>"
            f"<td>{escape(action.status)}</td>"
            f"<td>{escape(action.policy_decision or '-')}</td>"
            f"<td>{escape(action.risk_level or '-')}</td>"
            f"<td>{escape('-' if action.changed_path_count is None else str(action.changed_path_count))}</td>"
            f"<td>{escape(_join_or_dash(action.extension_buckets))}</td>"
            f"<td>{escape(action.interaction_id or '-')}</td>"
            f"<td>{escape(action.approval_status or '-')}</td>"
            f"<td>{escape(action.validation_id or '-')}</td>"
            f"<td>{escape(_summary_pairs(action.patch_summary))}</td>"
            "</tr>"
        )
    return (
        '<section aria-label="Action executions">'
        "<h2>Action Executions</h2>"
        '<table aria-label="Action execution summaries">'
        "<thead><tr><th>Action ID</th><th>Kind</th><th>Status</th>"
        "<th>Policy</th><th>Risk</th><th>Changed Paths</th>"
        "<th>Extensions</th><th>Approval Link</th><th>Approval</th>"
        "<th>Validation ID</th><th>Patch Summary</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _validation_summary_section(
    summary: ConsoleActionValidationSummary | None,
) -> str:
    if summary is None or not summary.validations:
        return _empty_state(
            "Validation Results",
            "No data loaded yet. No validation summaries are available.",
        )
    rows = []
    for outcome in summary.validations:
        rows.append(
            "<tr>"
            f"<td>{escape(outcome.label)}</td>"
            f"<td>{escape(outcome.status)}</td>"
            f"<td>{escape('-' if outcome.exit_code is None else str(outcome.exit_code))}</td>"
            f"<td>{escape('-' if outcome.duration_ms is None else str(outcome.duration_ms))}</td>"
            f"<td>{escape(outcome.policy_decision or '-')}</td>"
            f"<td>{escape(_summary_pairs(outcome.failure_summary))}</td>"
            f'<td><a href="/console/context?run_id={escape(summary.run_id or "")}">Context</a></td>'
            "</tr>"
        )
    status = summary.validation_status or "-"
    return (
        '<section aria-label="Validation results">'
        "<h2>Validation Results</h2>"
        f"<p>Status: <strong>{escape(status)}</strong></p>"
        '<table aria-label="Validation result summaries">'
        "<thead><tr><th>Label</th><th>Status</th><th>Exit Code</th>"
        "<th>Duration ms</th><th>Policy</th><th>Failure Summary</th>"
        "<th>Context Link</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _correlation_section(correlation: ConsoleCorrelationSummary | None) -> str:
    if correlation is None:
        return _empty_state(
            "Trace Correlation",
            "No data loaded yet. Select a run to view correlation metadata.",
        )
    return (
        '<section aria-label="Trace correlation">'
        "<h2>Trace Correlation</h2>"
        "<dl>"
        f"<dt>Session ID</dt><dd>{escape(correlation.session_id or '-')}</dd>"
        f"<dt>Run ID</dt><dd>{escape(correlation.run_id or '-')}</dd>"
        f"<dt>Tape ID</dt><dd>{escape(correlation.tape_id or '-')}</dd>"
        f"<dt>Topic ID</dt><dd>{escape(correlation.topic_id or '-')}</dd>"
        f"<dt>Retrieval ID</dt><dd>{escape(correlation.retrieval_id or '-')}</dd>"
        f"<dt>Action ID</dt><dd>{escape(correlation.action_id or '-')}</dd>"
        f"<dt>Validation ID</dt><dd>{escape(correlation.validation_id or '-')}</dd>"
        f"<dt>Interaction ID</dt><dd>{escape(correlation.interaction_id or '-')}</dd>"
        "</dl>"
        "</section>"
    )


def _observability_backend_section(summary: ConsoleObservabilitySummary) -> str:
    langfuse = (
        f'<a href="{escape(summary.langfuse_url)}">Langfuse</a>'
        if summary.langfuse_url
        else "not configured"
    )
    grafana = (
        f'<a href="{escape(summary.grafana_url)}">Grafana</a>'
        if summary.grafana_url
        else "not configured"
    )
    metrics_status = "enabled" if summary.metrics_enabled else "disabled"
    return (
        '<section aria-label="Observability backends">'
        "<h2>Backends</h2>"
        "<dl>"
        f"<dt>Metrics Endpoint</dt><dd>{escape(metrics_status)} at "
        f'<a href="{escape(summary.metrics_path)}">{escape(summary.metrics_path)}</a></dd>'
        f"<dt>Tracing Backend</dt><dd>{escape(summary.tracing_backend or 'not configured')}</dd>"
        f"<dt>Metrics Backend</dt><dd>{escape(summary.metrics_backend or 'not configured')}</dd>"
        f"<dt>Langfuse Link</dt><dd>{langfuse}</dd>"
        f"<dt>Grafana Link</dt><dd>{grafana}</dd>"
        "</dl>"
        "</section>"
    )


def _topic_table(topics: list[ConsoleTopicSummary]) -> str:
    if not topics:
        return _empty_state("Topics", "No data loaded yet. No topics are available.")
    rows = []
    for topic in topics:
        rows.append(
            "<tr>"
            f'<td><a href="/console/topics/{escape(topic.topic_id)}">'
            f"{escape(topic.topic_id)}</a></td>"
            f"<td>{escape(topic.session_id or '-')}</td>"
            f"<td>{escape(topic.tape_id or '-')}</td>"
            f"<td>{escape(topic.kind)}</td>"
            f'<td class="status">{escape(topic.status)}</td>'
            f"<td>{escape(topic.title or '-')}</td>"
            f"<td>{escape(_line_range(topic.topic_initial_seq, topic.topic_finalized_seq))}</td>"
            f"<td>{topic.run_count}</td>"
            f"<td>{escape('-' if topic.cost_total_tokens is None else str(topic.cost_total_tokens))}</td>"
            "</tr>"
        )
    return (
        '<section aria-label="Topic list">'
        "<h2>Topic List</h2>"
        '<table aria-label="Developer Console topics">'
        "<thead><tr><th>Topic ID</th><th>Session ID</th><th>Tape ID</th>"
        "<th>Kind</th><th>Status</th><th>Title</th><th>Range</th>"
        "<th>Runs</th><th>Total Tokens</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _topic_detail_metadata(topic: ConsoleTopicSummary) -> str:
    return (
        '<section aria-label="Topic range summary">'
        "<h2>Topic Range Summary</h2>"
        "<dl>"
        f"<dt>Topic ID</dt><dd>{escape(topic.topic_id)}</dd>"
        f"<dt>Session ID</dt><dd>{escape(topic.session_id or '-')}</dd>"
        f"<dt>Tape ID</dt><dd>{escape(topic.tape_id or '-')}</dd>"
        f"<dt>Kind</dt><dd>{escape(topic.kind)}</dd>"
        f"<dt>Status</dt><dd>{escape(topic.status)}</dd>"
        f"<dt>Title</dt><dd>{escape(topic.title or '-')}</dd>"
        f"<dt>Summary</dt><dd>{escape(topic.summary or '-')}</dd>"
        f"<dt>Range</dt><dd>{escape(_line_range(topic.topic_initial_seq, topic.topic_finalized_seq))}</dd>"
        "</dl>"
        "</section>"
    )


def _topic_anchor_table(anchors: tuple[ConsoleTopicAnchorSummary, ...]) -> str:
    if not anchors:
        return _empty_state("Topic Anchors", "No topic anchors are available.")
    rows = []
    for anchor in anchors:
        rows.append(
            "<tr>"
            f"<td>{escape('-' if anchor.seq is None else str(anchor.seq))}</td>"
            f"<td>{escape(anchor.anchor_type)}</td>"
            f"<td>{escape(anchor.entry_id or '-')}</td>"
            "</tr>"
        )
    return (
        '<section aria-label="Topic anchors">'
        "<h2>Topic Anchors</h2>"
        '<table aria-label="Topic anchors">'
        "<thead><tr><th>Seq</th><th>Anchor Type</th><th>Entry ID</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _topic_recall_table(recalls: tuple[ConsoleTopicRecallSummary, ...]) -> str:
    if not recalls:
        return _empty_state("Recall Links", "No topic recall links are available.")
    rows = []
    for recall in recalls:
        rows.append(
            "<tr>"
            f'<td><a href="/console/topics/{escape(recall.recalled_topic_id)}">'
            f"{escape(recall.recalled_topic_id)}</a></td>"
            f"<td>{escape(recall.relation)}</td>"
            f"<td>{escape('-' if recall.anchor_seq is None else str(recall.anchor_seq))}</td>"
            "</tr>"
        )
    return (
        '<section aria-label="Topic recall links">'
        "<h2>Recall Links</h2>"
        '<table aria-label="Topic recall links">'
        "<thead><tr><th>Recalled Topic</th><th>Relation</th><th>Anchor Seq</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _topic_cost_section(cost: ConsoleTopicCostSummary | None) -> str:
    if cost is None:
        return _empty_state("Topic Cost", "No topic cost aggregate is available.")
    return (
        '<section aria-label="Topic cost">'
        "<h2>Topic Cost</h2>"
        "<dl>"
        f"<dt>Prompt Tokens</dt><dd>{cost.prompt_tokens}</dd>"
        f"<dt>Completion Tokens</dt><dd>{cost.completion_tokens}</dd>"
        f"<dt>Total Tokens</dt><dd>{cost.total_tokens}</dd>"
        f"<dt>Runs</dt><dd>{cost.run_count}</dd>"
        f"<dt>Actions</dt><dd>{cost.action_count}</dd>"
        f"<dt>Validations</dt><dd>{cost.validation_count}</dd>"
        f"<dt>Tool Calls</dt><dd>{cost.tool_call_count}</dd>"
        "</dl>"
        "</section>"
    )


def _schedule_table(schedules: tuple[ConsoleScheduleSummary, ...]) -> str:
    if not schedules:
        return _empty_state(
            "Scheduled Runs",
            "No data loaded yet. No schedules are available.",
        )
    rows = []
    for schedule in schedules:
        rows.append(
            "<tr>"
            f"<td>{escape(schedule.schedule_id)}</td>"
            f"<td>{escape(schedule.session_id)}</td>"
            f"<td>{escape(schedule.topic_id or '-')}</td>"
            f"<td>{escape(schedule.kind)}</td>"
            f'<td class="status">{escape(schedule.status)}</td>'
            f"<td>{escape(schedule.cadence)}</td>"
            f"<td>{escape(schedule.title or '-')}</td>"
            f"<td>{escape(_format_optional_datetime(schedule.next_due_at))}</td>"
            f"<td>{escape(_format_optional_datetime(schedule.last_triggered_at))}</td>"
            "</tr>"
        )
    return (
        '<section aria-label="Scheduled runs">'
        "<h2>Scheduled Runs</h2>"
        '<table aria-label="Developer Console schedules">'
        "<thead><tr><th>Schedule ID</th><th>Session ID</th><th>Topic ID</th>"
        "<th>Kind</th><th>Status</th><th>Cadence</th><th>Title</th>"
        "<th>Next Due</th><th>Last Triggered</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _schedule_trigger_table(
    triggers: tuple[ConsoleScheduleTriggerSummary, ...],
) -> str:
    if not triggers:
        return _empty_state(
            "Schedule Triggers",
            "No data loaded yet. No schedule triggers are available.",
        )
    rows = []
    for trigger in triggers:
        run_cell = (
            f'<a href="/console/runs/{escape(trigger.run_id)}">'
            f"{escape(trigger.run_id)}</a>"
            if trigger.run_id
            else "-"
        )
        rows.append(
            "<tr>"
            f"<td>{escape(trigger.trigger_id)}</td>"
            f"<td>{escape(trigger.schedule_id)}</td>"
            f"<td>{escape(trigger.signal_id or '-')}</td>"
            f"<td>{escape(trigger.topic_id or '-')}</td>"
            f"<td>{run_cell}</td>"
            f'<td class="status">{escape(trigger.status)}</td>'
            f"<td>{escape(trigger.reason or '-')}</td>"
            f"<td>{escape(_format_datetime(trigger.due_at))}</td>"
            f"<td>{escape(_format_datetime(trigger.planned_at))}</td>"
            "</tr>"
        )
    return (
        '<section aria-label="Schedule triggers">'
        "<h2>Schedule Triggers</h2>"
        '<table aria-label="Developer Console schedule triggers">'
        "<thead><tr><th>Trigger ID</th><th>Schedule ID</th><th>Signal ID</th>"
        "<th>Topic ID</th><th>Run ID</th><th>Status</th><th>Reason</th>"
        "<th>Due</th><th>Planned</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _proactive_signal_table(
    signals: tuple[ConsoleProactiveSignalSummary, ...],
) -> str:
    if not signals:
        return _empty_state(
            "Proactive Signals",
            "No data loaded yet. No proactive signals are available.",
        )
    rows = []
    for signal in signals:
        rows.append(
            "<tr>"
            f"<td>{escape(signal.signal_id)}</td>"
            f"<td>{escape(signal.session_id or '-')}</td>"
            f"<td>{escape(signal.topic_id or '-')}</td>"
            f"<td>{escape(signal.kind)}</td>"
            f'<td class="status">{escape(signal.status)}</td>'
            f"<td>{escape(_format_datetime(signal.observed_at))}</td>"
            f"<td>{escape(_format_optional_datetime(signal.cooldown_until))}</td>"
            f"<td>{escape(signal.summary or '-')}</td>"
            "</tr>"
        )
    return (
        '<section aria-label="Proactive signals">'
        "<h2>Proactive Signals</h2>"
        '<table aria-label="Developer Console proactive signals">'
        "<thead><tr><th>Signal ID</th><th>Session ID</th><th>Topic ID</th>"
        "<th>Kind</th><th>Status</th><th>Observed</th><th>Cooldown Until</th>"
        "<th>Summary</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _bee_task_table(tasks: tuple[ConsoleBeeTaskSummary, ...]) -> str:
    if not tasks:
        return _empty_state(
            "Bee Task List", "No data loaded yet. No Bee tasks are available."
        )
    rows = []
    for task in tasks:
        rows.append(
            "<tr>"
            f"<td>{escape(task.task_id)}</td>"
            f"<td>{escape(task.topic_id or '-')}</td>"
            f"<td>{escape(task.session_id or '-')}</td>"
            f"<td>{escape(task.kind)}</td>"
            f"<td>{escape(task.profile)}</td>"
            f'<td class="status">{escape(task.status)}</td>'
            f"<td>{task.node_count}</td>"
            f"<td>{task.run_count}</td>"
            "</tr>"
        )
    return (
        '<section aria-label="Bee task list">'
        "<h2>Bee Task List</h2>"
        '<table aria-label="Developer Console Bee tasks">'
        "<thead><tr><th>Task ID</th><th>Topic ID</th><th>Session ID</th>"
        "<th>Kind</th><th>Profile</th><th>Status</th><th>Nodes</th><th>Runs</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _bee_node_table(nodes: tuple[ConsoleBeeNodeSummary, ...]) -> str:
    if not nodes:
        return _empty_state(
            "Bee Node Launches",
            "No data loaded yet. No Bee node launch references are available.",
        )
    rows = []
    for node in nodes:
        run_cell = (
            f'<a href="/console/runs/{escape(node.run_id)}">{escape(node.run_id)}</a>'
            if node.run_id
            else "-"
        )
        rows.append(
            "<tr>"
            f"<td>{escape(node.task_id)}</td>"
            f"<td>{escape(node.node_id)}</td>"
            f"<td>{run_cell}</td>"
            f"<td>{escape(node.kind)}</td>"
            f"<td>{escape(node.profile)}</td>"
            f'<td class="status">{escape(node.status)}</td>'
            f"<td>{escape(node.context_profile or '-')}</td>"
            f"<td>{escape(node.validation_profile or '-')}</td>"
            f"<td>{escape(node.workspace_policy or '-')}</td>"
            f"<td>{escape(node.approval_policy or '-')}</td>"
            f"<td>{escape(node.action_policy or '-')}</td>"
            f"<td>{escape(node.workspace_binding or '-')}</td>"
            "</tr>"
        )
    return (
        '<section aria-label="Bee node launches">'
        "<h2>Bee Node Launches</h2>"
        '<table aria-label="Developer Console Bee nodes">'
        "<thead><tr><th>Task ID</th><th>Node ID</th><th>Run ID</th>"
        "<th>Kind</th><th>Profile</th><th>Status</th><th>Context</th>"
        "<th>Validation</th><th>Workspace Policy</th><th>Approval Policy</th>"
        "<th>Action Policy</th><th>Workspace Binding</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _bee_template_table(templates: tuple[ConsoleBeeTemplateSummary, ...]) -> str:
    if not templates:
        return _empty_state(
            "Bee Workspace Templates",
            "No data loaded yet. No workspace Bee templates are available.",
        )
    rows = []
    for template in templates:
        rows.append(
            "<tr>"
            f"<td>{escape(template.template_id)}</td>"
            f"<td>{escape(template.kind)}</td>"
            f"<td>{escape(template.profile)}</td>"
            f"<td>{escape(template.title)}</td>"
            f"<td>{template.feature_count}</td>"
            f"<td>{'yes' if template.has_commands else 'no'}</td>"
            f"<td>{template.command_count}</td>"
            "</tr>"
        )
    return (
        '<section aria-label="Bee workspace templates">'
        "<h2>Bee Workspace Templates</h2>"
        '<table aria-label="Developer Console Bee workspace templates">'
        "<thead><tr><th>Template ID</th><th>Kind</th><th>Profile</th>"
        "<th>Title</th><th>Features</th><th>Commands File</th>"
        "<th>Command Intents</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _bee_pack_table(packs: tuple[ConsoleBeePackSummary, ...]) -> str:
    if not packs:
        return _empty_state(
            "Bee Template Packs",
            "No data loaded yet. No Bee template packs are available.",
        )
    rows = []
    for pack in packs:
        rows.append(
            "<tr>"
            f"<td>{escape(pack.pack_id)}</td>"
            f"<td>{escape(pack.name)}</td>"
            f"<td>{escape(pack.version)}</td>"
            f"<td>{escape(pack.source_type)}</td>"
            f"<td>{escape(pack.domain_profile or '-')}</td>"
            f"<td>{escape(_join_or_dash(pack.tags))}</td>"
            f"<td>{pack.template_count}</td>"
            "</tr>"
        )
    return (
        '<section aria-label="Bee template packs">'
        "<h2>Bee Template Packs</h2>"
        '<table aria-label="Developer Console Bee template packs">'
        "<thead><tr><th>Pack ID</th><th>Name</th><th>Version</th>"
        "<th>Source</th><th>Domain Profile</th><th>Tags</th>"
        "<th>Templates</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _bee_pack_template_table(
    templates: tuple[ConsoleBeePackTemplateSummary, ...],
) -> str:
    if not templates:
        return _empty_state(
            "Bee Pack Templates",
            "No data loaded yet. No Bee pack templates are available.",
        )
    rows = []
    for template in templates:
        rows.append(
            "<tr>"
            f"<td>{escape(template.pack_id)}</td>"
            f"<td>{escape(template.template_id)}</td>"
            f"<td>{escape(template.source_type)}</td>"
            f"<td>{escape(template.kind)}</td>"
            f"<td>{escape(template.profile)}</td>"
            f"<td>{escape(template.title)}</td>"
            "</tr>"
        )
    return (
        '<section aria-label="Bee pack templates">'
        "<h2>Bee Pack Templates</h2>"
        '<table aria-label="Developer Console Bee pack templates">'
        "<thead><tr><th>Pack ID</th><th>Template ID</th><th>Source</th>"
        "<th>Kind</th><th>Profile</th><th>Title</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _bee_pack_compatibility_table(
    reports: tuple[ConsoleBeePackCompatibilitySummary, ...],
) -> str:
    if not reports:
        return _empty_state(
            "Bee Pack Compatibility",
            "No data loaded yet. No Bee pack compatibility reports are available.",
        )
    rows = []
    for report in reports:
        rows.append(
            "<tr>"
            f"<td>{escape(report.pack_id or '-')}</td>"
            f"<td>{escape(report.source_type)}</td>"
            f'<td class="status">{escape(report.status)}</td>'
            f"<td>{report.check_count}</td>"
            f"<td>{report.finding_count}</td>"
            f"<td>{report.template_count}</td>"
            f"<td>{escape(_join_or_dash(report.recommended_fixes))}</td>"
            "</tr>"
        )
    return (
        '<section aria-label="Bee pack compatibility">'
        "<h2>Bee Pack Compatibility</h2>"
        '<table aria-label="Developer Console Bee pack compatibility">'
        "<thead><tr><th>Pack ID</th><th>Source</th><th>Status</th>"
        "<th>Checks</th><th>Findings</th><th>Templates</th>"
        "<th>Recommended Fixes</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _bee_pack_dry_run_table(
    plans: tuple[ConsoleBeePackDryRunSummary, ...],
) -> str:
    if not plans:
        return _empty_state(
            "Bee Pack Dry-Run Plans",
            "No data loaded yet. No Bee pack dry-run plans are available.",
        )
    rows = []
    for plan in plans:
        rows.append(
            "<tr>"
            f"<td>{escape(plan.pack_id)}</td>"
            f"<td>{escape(plan.template_id)}</td>"
            f"<td>{escape(plan.source_type)}</td>"
            f'<td class="status">{escape(plan.status)}</td>'
            f"<td>{escape(plan.task_json_path)}</td>"
            f"<td>{escape(plan.report_path)}</td>"
            f"<td>{escape(plan.evidence_dir)}</td>"
            f"<td>{escape(plan.memory_candidates_path)}</td>"
            f"<td>{plan.node_count}</td>"
            f"<td>{plan.command_count}</td>"
            f"<td>{plan.warning_count}</td>"
            "</tr>"
        )
    return (
        '<section aria-label="Bee pack dry-run plans">'
        "<h2>Bee Pack Dry-Run Plans</h2>"
        '<table aria-label="Developer Console Bee pack dry-run plans">'
        "<thead><tr><th>Pack ID</th><th>Template ID</th><th>Source</th>"
        "<th>Status</th><th>Task JSON</th><th>Report</th><th>Evidence</th>"
        "<th>Memory Candidates</th><th>Nodes</th><th>Commands</th>"
        "<th>Warnings</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _bee_run_artifact_table(
    artifacts: tuple[ConsoleBeeRunArtifactSummary, ...],
) -> str:
    if not artifacts:
        return _empty_state(
            "Bee Workspace Run Artifacts",
            "No data loaded yet. No workspace Bee run artifacts are available.",
        )
    rows = []
    for artifact in artifacts:
        rows.append(
            "<tr>"
            f"<td>{escape(artifact.task_id)}</td>"
            f"<td>{escape(artifact.template_id)}</td>"
            f"<td>{escape(artifact.topic_id)}</td>"
            f'<td class="status">{escape(artifact.status)}</td>'
            f"<td>{artifact.node_count}</td>"
            f"<td>{artifact.run_count}</td>"
            f"<td>{artifact.action_count}</td>"
            f"<td>{artifact.validation_count}</td>"
            f"<td>{artifact.executor_count}</td>"
            f"<td>{'yes' if artifact.has_report else 'no'}</td>"
            f"<td>{'yes' if artifact.has_memory_candidates else 'no'}</td>"
            "</tr>"
        )
    return (
        '<section aria-label="Bee workspace run artifacts">'
        "<h2>Bee Workspace Run Artifacts</h2>"
        '<table aria-label="Developer Console Bee workspace run artifacts">'
        "<thead><tr><th>Task ID</th><th>Template ID</th><th>Topic ID</th>"
        "<th>Status</th><th>Nodes</th><th>Runs</th><th>Actions</th>"
        "<th>Validations</th><th>Executors</th><th>Report</th>"
        "<th>Memory Candidates</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _bee_command_intent_table(
    commands: tuple[ConsoleBeeCommandIntentSummary, ...],
) -> str:
    if not commands:
        return _empty_state(
            "Bee Workspace Command Intents",
            "No data loaded yet. No workspace Bee command intents are available.",
        )
    rows = []
    for command in commands:
        rows.append(
            "<tr>"
            f"<td>{escape(command.template_id)}</td>"
            f"<td>{escape(command.name)}</td>"
            f"<td>{escape(command.profile)}</td>"
            f"<td>{escape(command.policy)}</td>"
            f"<td>{escape(command.category)}</td>"
            f"<td>{escape(command.validation_label or '-')}</td>"
            f'<td class="status">{escape(command.status)}</td>'
            f'<td class="status">{escape(command.bridge_status or "-")}</td>'
            f"<td>{escape(command.approval_route or '-')}</td>"
            f'<td class="status">{escape(command.evidence_status or "-")}</td>'
            "</tr>"
        )
    return (
        '<section aria-label="Bee workspace command intents">'
        "<h2>Bee Workspace Command Intents</h2>"
        '<table aria-label="Developer Console Bee workspace command intents">'
        "<thead><tr><th>Template ID</th><th>Name</th><th>Profile</th>"
        "<th>Policy</th><th>Category</th><th>Validation</th>"
        "<th>Status</th><th>Bridge</th><th>Approval</th><th>Evidence</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _bee_launch_table(launches: tuple[ConsoleBeeLaunchSummary, ...]) -> str:
    if not launches:
        return _empty_state(
            "Bee Launches",
            "No data loaded yet. No Bee launches are available.",
        )
    rows = []
    for launch in launches:
        rows.append(
            "<tr>"
            f"<td>{escape(launch.launch_id)}</td>"
            f"<td>{escape(launch.source)}</td>"
            f'<td class="status">{escape(launch.status)}</td>'
            f"<td>{escape(launch.template_id or '-')}</td>"
            f"<td>{escape(launch.task_id or '-')}</td>"
            f"<td>{escape(launch.topic_id or '-')}</td>"
            f"<td>{escape(launch.schedule_id or '-')}</td>"
            f"<td>{escape(launch.signal_id or '-')}</td>"
            f"<td>{escape(launch.error_summary or '-')}</td>"
            "</tr>"
        )
    return (
        '<section aria-label="Bee launches">'
        "<h2>Bee Launches</h2>"
        '<table aria-label="Developer Console Bee launches">'
        "<thead><tr><th>Launch ID</th><th>Source</th><th>Status</th>"
        "<th>Template ID</th><th>Task ID</th><th>Topic ID</th>"
        "<th>Schedule ID</th><th>Signal ID</th><th>Error</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _executor_run_table(executor_runs: tuple[ConsoleExecutorRunSummary, ...]) -> str:
    if not executor_runs:
        return _empty_state(
            "Executor Runs",
            "No data loaded yet. No external executor runs are available.",
        )
    rows = []
    for executor_run in executor_runs:
        rows.append(
            "<tr>"
            f"<td>{escape(executor_run.executor_run_id)}</td>"
            f"<td>{escape(executor_run.executor_kind)}</td>"
            f'<td class="status">{escape(executor_run.status)}</td>'
            f"<td>{escape(executor_run.capability_status or '-')}</td>"
            f"<td>{escape(executor_run.task_id or '-')}</td>"
            f"<td>{escape(executor_run.node_id or '-')}</td>"
            f"<td>{escape(executor_run.launch_id or '-')}</td>"
            f"<td>{escape(executor_run.topic_id or '-')}</td>"
            f"<td>{escape(executor_run.sanitized_summary or '-')}</td>"
            "</tr>"
        )
    return (
        '<section aria-label="Executor runs">'
        "<h2>Executor Runs</h2>"
        '<table aria-label="Developer Console executor runs">'
        "<thead><tr><th>Executor Run ID</th><th>Kind</th><th>Status</th>"
        "<th>Capability</th><th>Task ID</th><th>Node ID</th>"
        "<th>Launch ID</th><th>Topic ID</th><th>Summary</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _topic_related_runs_section(runs: tuple[ConsoleRunSummary, ...]) -> str:
    if not runs:
        return _empty_state("Runs Under Topic", "No related runs are available.")
    return (
        '<section aria-label="Runs under topic"><h2>Runs Under Topic</h2>'
        + _run_table(list(runs))
        + "</section>"
    )


def _topic_related_actions_section(
    actions: tuple[ConsoleActionSummary, ...],
) -> str:
    return _action_summary_section(
        ConsoleActionValidationSummary(
            run_id=None,
            actions=actions,
            validation_status=None,
            validations=(),
        )
    )


def _topic_related_validations_section(
    validations: tuple[ConsoleValidationOutcomeSummary, ...],
    *,
    run_id: str | None,
) -> str:
    return _validation_summary_section(
        ConsoleActionValidationSummary(
            run_id=run_id,
            actions=(),
            validation_status=None,
            validations=validations,
        )
    )


def _first_run_id(runs: tuple[ConsoleRunSummary, ...]) -> str | None:
    if not runs:
        return None
    return runs[0].run_id


def _workspace_capability_section(
    capability: ConsoleWorkspaceCapabilitySummary | None,
) -> str:
    if capability is None:
        return _empty_state(
            "Workspace Provider",
            "Workspace provider capabilities are not configured.",
        )
    available = "available" if capability.available else "unavailable"
    supported = tuple(
        name
        for name, enabled in (
            ("provision", capability.supports_provision),
            ("archive", capability.supports_archive),
            ("diff", capability.supports_diff),
            ("patch", capability.supports_patch),
            ("publish", capability.supports_publish),
        )
        if enabled
    )
    return (
        '<section aria-label="Workspace provider capabilities">'
        "<h2>Workspace Provider</h2>"
        "<dl>"
        f"<dt>Provider</dt><dd>{escape(capability.provider)}</dd>"
        f"<dt>Status</dt><dd>{escape(available)}</dd>"
        f"<dt>Reason</dt><dd>{escape(capability.reason)}</dd>"
        f"<dt>Supported Operations</dt><dd>{escape(_join_or_dash(supported))}</dd>"
        "</dl>"
        "</section>"
    )


def _workspace_table(workspaces: list[ConsoleWorkspaceSummary]) -> str:
    if not workspaces:
        return _empty_state(
            "Workspace Inventory",
            "No data loaded yet. No workspaces are available.",
        )
    rows = []
    for workspace in workspaces:
        rows.append(
            "<tr>"
            f"<td>{escape(workspace.workspace_id)}</td>"
            f'<td class="status">{escape(workspace.status)}</td>'
            f"<td>{escape(_format_datetime(workspace.updated_at))}</td>"
            f"<td>{escape(workspace.session_id or '-')}</td>"
            f"<td>{escape(workspace.provider or '-')}</td>"
            f"<td>{escape(workspace.provider_instance_id or '-')}</td>"
            f"<td>{escape(workspace.workspace_host_label or '-')}</td>"
            f"<td>{escape(workspace.source_kind or '-')}</td>"
            f"<td>{escape(workspace.retention_policy or '-')}</td>"
            f"<td>{escape(_format_optional_datetime(workspace.expires_at))}</td>"
            f"<td>{escape(_optional_bool(workspace.is_local))}</td>"
            f"<td>{escape(_join_or_dash(workspace.result_ref_keys))}</td>"
            f"<td>{escape(workspace.cleanup_error or '-')}</td>"
            "</tr>"
        )
    return (
        '<section aria-label="Workspace inventory">'
        "<h2>Workspace Inventory</h2>"
        '<table aria-label="Developer Console workspaces">'
        "<thead><tr><th>Workspace ID</th><th>Status</th><th>Updated</th>"
        "<th>Session ID</th><th>Provider</th><th>Provider Instance</th>"
        "<th>Host Label</th><th>Source</th><th>Retention</th>"
        "<th>Expires</th><th>Local</th><th>Result Ref Keys</th>"
        "<th>Cleanup Error</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _health_section(summary: ConsoleReleaseSummary) -> str:
    checks = "".join(
        f"<li>{escape(name)}={escape(status)}</li>"
        for name, status in summary.readiness_checks
    )
    return (
        '<section aria-label="Health and readiness">'
        "<h2>Health / Readiness</h2>"
        "<dl>"
        f"<dt>Health</dt><dd>{escape(summary.health_status)}</dd>"
        f"<dt>Sessions</dt><dd>{summary.session_count}</dd>"
        f"<dt>Version</dt><dd>{escape(summary.version)}</dd>"
        f"<dt>Readiness</dt><dd>{escape(summary.readiness_status)}</dd>"
        "</dl>"
        f'<ul class="pill-list">{checks}</ul>'
        "</section>"
    )


def _release_gate_section(summary: ConsoleReleaseSummary) -> str:
    if not summary.release_gates:
        return _empty_state(
            "Release Verification",
            "No release verification manifest is available.",
        )
    rows = []
    for gate in summary.release_gates:
        rows.append(
            "<tr>"
            f"<td>{escape(gate.gate_id)}</td>"
            f"<td>{escape(gate.scope)}</td>"
            f"<td>{escape('yes' if gate.required else 'no')}</td>"
            f"<td>{escape(gate.command)}</td>"
            "</tr>"
        )
    title = summary.release_manifest_name or "Release Verification"
    return (
        '<section aria-label="Release verification">'
        f"<h2>{escape(title)}</h2>"
        '<table aria-label="Release verification gates">'
        "<thead><tr><th>Gate</th><th>Scope</th><th>Required</th><th>Command</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _summary_pairs(values: tuple[tuple[str, str], ...]) -> str:
    if not values:
        return "-"
    return ", ".join(f"{key}={value}" for key, value in values)


def _run_snapshot_section(snapshot: ConsoleSnapshotSummary | None) -> str:
    if snapshot is None:
        return _empty_state(
            "Message Snapshot", "No latest message snapshot is available."
        )
    labels = "".join(f"<li>{escape(label)}</li>" for label in snapshot.message_labels)
    metadata_keys = _join_or_dash(snapshot.metadata_keys)
    return (
        '<section aria-label="Message snapshot">'
        "<h2>Message Snapshot</h2>"
        "<dl>"
        f"<dt>Snapshot ID</dt><dd>{escape(snapshot.snapshot_id)}</dd>"
        f"<dt>Created</dt><dd>{escape(_format_optional_datetime(snapshot.created_at))}</dd>"
        f"<dt>Messages</dt><dd>{snapshot.message_count} messages</dd>"
        f"<dt>Metadata Keys</dt><dd>{escape(metadata_keys)}</dd>"
        "</dl>"
        f'<ul class="pill-list">{labels}</ul>'
        "</section>"
    )


def _run_events_section(
    run_id: str,
    events: tuple[ConsoleDisplayEventSummary, ...],
) -> str:
    replay_note = (
        "Display event replay uses the "
        f"/runs/{escape(run_id)}/display-events API. Pass last_event_id to resume "
        "after a known source event."
    )
    if not events:
        return _empty_state("Display Events", replay_note)
    rows = []
    for event in events:
        rows.append(
            "<tr>"
            f"<td>{escape(str(event.sequence or '-'))}</td>"
            f"<td>{escape(event.display_kind)}</td>"
            f"<td>{escape(event.source_event_id)}</td>"
            f"<td>{escape(_format_datetime(event.created_at))}</td>"
            f"<td>{escape(_join_or_dash(event.payload_keys))}</td>"
            "</tr>"
        )
    return (
        '<section aria-label="Display events">'
        "<h2>Display Events</h2>"
        f'<p class="lede">{replay_note}</p>'
        '<table aria-label="Run display events">'
        "<thead><tr><th>Sequence</th><th>Kind</th><th>Source Event ID</th>"
        "<th>Created</th><th>Payload Keys</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _run_detail_links(detail: ConsoleRunDetail) -> str:
    topic_link = ""
    if detail.metadata_keys and "topic_id" in detail.metadata_keys:
        topic_link = '<li><a href="/console/topics">Topics</a></li>'
    return (
        '<section aria-label="Related console links">'
        "<h2>Related Views</h2>"
        '<ul class="pill-list">'
        f"{topic_link}"
        f'<li><a href="/console/tape">Tape {escape(detail.tape_id or "-")}</a></li>'
        f'<li><a href="/console/context?run_id={escape(detail.run_id)}">Context</a></li>'
        f'<li><a href="/console/actions?run_id={escape(detail.run_id)}">Actions</a></li>'
        f'<li><a href="/console/observability?run_id={escape(detail.run_id)}">Observability</a></li>'
        "</ul>"
        "</section>"
    )


def _session_table(sessions: list[ConsoleSessionSummary]) -> str:
    if not sessions:
        return _empty_state("No data loaded yet.", "No sessions are available.")
    rows = []
    for session in sessions:
        rows.append(
            "<tr>"
            f"<td>{escape(session.session_id)}</td>"
            f'<td class="status">{escape(session.status)}</td>'
            f"<td>{escape(session.turn_status)}</td>"
            f"<td>{escape(_format_datetime(session.created_at))}</td>"
            f"<td>{escape(_format_datetime(session.updated_at))}</td>"
            f"<td>{escape(session.current_turn_id or '-')}</td>"
            "</tr>"
        )
    return (
        '<table aria-label="Developer Console sessions">'
        "<thead><tr>"
        "<th>Session ID</th><th>Status</th><th>Turn Status</th>"
        "<th>Created</th><th>Updated</th><th>Turn ID</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def _run_table(runs: list[ConsoleRunSummary]) -> str:
    if not runs:
        return _empty_state("No data loaded yet.", "No runtime runs are available.")
    rows = []
    for run in runs:
        error = run.error_summary or "-"
        rows.append(
            "<tr>"
            f'<td><a href="/console/runs/{escape(run.run_id)}">'
            f"{escape(run.run_id)}</a></td>"
            f"<td>{escape(run.session_id)}</td>"
            f'<td class="status">{escape(run.status)}</td>'
            f"<td>{escape(_format_datetime(run.started_at))}</td>"
            f"<td>{escape(_format_optional_datetime(run.ended_at))}</td>"
            f"<td>{escape(error)}</td>"
            "</tr>"
        )
    return (
        '<table aria-label="Developer Console runs">'
        "<thead><tr>"
        "<th>Run ID</th><th>Session ID</th><th>Status</th>"
        "<th>Started</th><th>Finished</th><th>Error Summary</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def _empty_state(title: str, description: str) -> str:
    return (
        '<section class="empty-state">'
        f"<h2>{escape(title)}</h2>"
        f"<p>{escape(description)}</p>"
        "</section>"
    )


def _format_optional_datetime(value: datetime | None) -> str:
    if value is None:
        return "-"
    return _format_datetime(value)


def _format_datetime(value: datetime) -> str:
    return value.isoformat()


def _optional_bool(value: bool | None) -> str:
    if value is None:
        return "-"
    return "yes" if value else "no"


def _join_or_dash(values: tuple[str, ...]) -> str:
    if not values:
        return "-"
    return ", ".join(values)


def _line_range(line_start: int | None, line_end: int | None) -> str:
    if line_start is None and line_end is None:
        return "-"
    if line_start is None:
        return f"-{line_end}"
    if line_end is None:
        return f"{line_start}-"
    return f"{line_start}-{line_end}"


def _navigation(active_path: str) -> str:
    overview_active = " active" if active_path == "/console" else ""
    links = [f'<a class="{overview_active.strip()}" href="/console">Overview</a>']
    for page in CONSOLE_PAGES:
        active = " active" if page.path == active_path else ""
        links.append(
            f'<a class="{active.strip()}" href="{escape(page.path)}">'
            f"{escape(page.nav_label)}</a>"
        )
    return (
        '<nav aria-label="Developer Console navigation">'
        "<h2>Developer Console</h2>"
        f"{''.join(links)}"
        "</nav>"
    )
