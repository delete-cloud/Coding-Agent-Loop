"""Server-rendered Developer Console pages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape


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
class ConsoleEventSummary:
    sequence: int | None
    event_id: str
    event_kind: str
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
    events: tuple[ConsoleEventSummary, ...]


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


def safe_error_summary(error: str | None) -> str | None:
    if error is None:
        return None
    normalized = error.lower()
    forbidden = (
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
    if any(token in normalized for token in forbidden):
        return "Sensitive error summary redacted."
    return error[:240]


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
    forbidden = (
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
    return any(token in normalized for token in forbidden)


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


def _run_events_section(run_id: str, events: tuple[ConsoleEventSummary, ...]) -> str:
    replay_note = (
        "Runtime event replay uses the existing "
        f"/runs/{escape(run_id)}/events API. Pass last_event_id to resume after a known event."
    )
    if not events:
        return _empty_state("Runtime Events", replay_note)
    rows = []
    for event in events:
        rows.append(
            "<tr>"
            f"<td>{escape(str(event.sequence or '-'))}</td>"
            f"<td>{escape(event.event_kind)}</td>"
            f"<td>{escape(event.event_id)}</td>"
            f"<td>{escape(_format_datetime(event.created_at))}</td>"
            f"<td>{escape(_join_or_dash(event.payload_keys))}</td>"
            "</tr>"
        )
    return (
        '<section aria-label="Runtime events">'
        "<h2>Runtime Events</h2>"
        f'<p class="lede">{replay_note}</p>'
        '<table aria-label="Run runtime events">'
        "<thead><tr><th>Sequence</th><th>Kind</th><th>Event ID</th>"
        "<th>Created</th><th>Payload Keys</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _run_detail_links(detail: ConsoleRunDetail) -> str:
    return (
        '<section aria-label="Related console links">'
        "<h2>Related Views</h2>"
        '<ul class="pill-list">'
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


def _join_or_dash(values: tuple[str, ...]) -> str:
    if not values:
        return "-"
    return ", ".join(values)


def _navigation(active_path: str) -> str:
    links = ['<a href="/console">Overview</a>']
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
