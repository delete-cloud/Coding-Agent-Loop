"""Server-rendered Developer Console pages."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape


@dataclass(frozen=True)
class ConsolePage:
    path: str
    title: str
    nav_label: str
    description: str


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
