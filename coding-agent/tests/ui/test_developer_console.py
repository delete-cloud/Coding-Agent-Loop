from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from coding_agent.runtime_store import (
    AgentInteractionRecord,
    AgentRunRecord,
    RunMessageSnapshotRecord,
    RuntimeEventRecord,
)
from coding_agent.ui.http_server import app, session_manager
from coding_agent.ui.session_manager import Session


CONSOLE_ROUTES = (
    "/console",
    "/console/sessions",
    "/console/runs",
    "/console/interactions",
    "/console/tape",
    "/console/context",
    "/console/memory",
    "/console/actions",
    "/console/observability",
    "/console/release",
)

NAV_LINKS = {
    "Sessions": "/console/sessions",
    "Runs": "/console/runs",
    "HITL / Interactions": "/console/interactions",
    "Tape": "/console/tape",
    "Context": "/console/context",
    "Memory": "/console/memory",
    "Actions / Validation": "/console/actions",
    "Observability": "/console/observability",
    "Release / Health": "/console/release",
}

FORBIDDEN_RENDERED_TEXT = (
    "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
    "raw prompt",
    "raw message",
    "command_output",
    "stdout",
    "stderr",
    "env",
)


class _ConsoleRuntimeStore:
    def __init__(self, runs: list[AgentRunRecord] | None = None) -> None:
        self.runs = {run.run_id: run for run in runs or []}
        self.events: dict[str, list[RuntimeEventRecord]] = {}
        self.snapshots: dict[str, RunMessageSnapshotRecord] = {}

    async def create_agent_run(self, record: AgentRunRecord) -> AgentRunRecord:
        self.runs[record.run_id] = record
        return record

    async def load_agent_run(self, run_id: str) -> AgentRunRecord | None:
        return self.runs.get(run_id)

    async def list_agent_runs(self, session_id: str) -> list[AgentRunRecord]:
        return [run for run in self.runs.values() if run.session_id == session_id]

    async def update_agent_run(
        self,
        run_id: str,
        *,
        status: str,
        ended_at: datetime | None,
        metadata: dict[str, object],
        result: dict[str, object],
        error: str | None,
    ) -> AgentRunRecord:
        current = self.runs[run_id]
        updated = AgentRunRecord(
            run_id=current.run_id,
            session_id=current.session_id,
            tape_id=current.tape_id,
            parent_run_id=current.parent_run_id,
            agent_id=current.agent_id,
            status=status,
            started_at=current.started_at,
            ended_at=ended_at,
            metadata=metadata,
            result=result,
            error=error,
        )
        self.runs[run_id] = updated
        return updated

    async def append_runtime_event(
        self,
        record: RuntimeEventRecord,
    ) -> RuntimeEventRecord:
        self.events.setdefault(record.run_id, []).append(record)
        return record

    async def load_runtime_event(self, event_id: str) -> RuntimeEventRecord | None:
        for events in self.events.values():
            for event in events:
                if event.event_id == event_id:
                    return event
        return None

    async def replay_runtime_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 1000,
    ) -> list[RuntimeEventRecord]:
        events = self.events.get(run_id, [])
        return [
            event
            for event in events
            if event.sequence is not None and event.sequence > after_sequence
        ][:limit]

    async def save_message_snapshot(
        self,
        record: RunMessageSnapshotRecord,
    ) -> RunMessageSnapshotRecord:
        self.snapshots[record.snapshot_id] = record
        return record

    async def load_message_snapshot(
        self,
        snapshot_id: str,
    ) -> RunMessageSnapshotRecord | None:
        return self.snapshots.get(snapshot_id)

    async def create_agent_interaction(
        self,
        record: AgentInteractionRecord,
    ) -> AgentInteractionRecord:
        return record

    async def resolve_agent_interaction(
        self,
        interaction_id: str,
        *,
        status: str,
        response_payload: dict[str, object],
        resolved_at: datetime,
    ) -> AgentInteractionRecord:
        raise KeyError(interaction_id)

    async def list_agent_interactions(
        self,
        run_id: str,
    ) -> list[AgentInteractionRecord]:
        return []


@pytest.fixture(autouse=True)
async def clear_console_state() -> AsyncIterator[None]:
    session_manager.configure_runtime_store(None)
    session_manager.clear_sessions()
    yield
    session_manager.configure_runtime_store(None)
    session_manager.clear_sessions()


def _register_console_session(
    session_id: str,
    *,
    status: str = "created",
) -> Session:
    created_at = datetime(2026, 5, 20, 1, 2, 3, tzinfo=UTC)
    session = Session(
        id=session_id,
        created_at=created_at,
        last_activity=datetime(2026, 5, 20, 1, 3, 4, tzinfo=UTC),
        provider_name="fixture-provider",
        model_name="fixture-model",
    )
    if status == "running":
        session.turn_in_progress = True
        session.turn_status = "running"
        session.current_turn_id = f"{session_id}-turn"
    elif status == "failed":
        session.turn_status = "failed"
        session.last_failure_details = "hidden failure details"
    elif status == "waiting_approval":
        session.pending_approval = {"request_id": "approval-secret-payload"}
    session_manager.register_session(session)
    return session


def _runtime_run(
    run_id: str,
    session_id: str,
    *,
    status: str,
    error: str | None = None,
) -> AgentRunRecord:
    return AgentRunRecord(
        run_id=run_id,
        session_id=session_id,
        tape_id=f"{session_id}-tape",
        parent_run_id=None,
        agent_id=None,
        status=status,
        started_at=datetime(2026, 5, 20, 2, 0, 0, tzinfo=UTC),
        ended_at=(
            None if status == "running" else datetime(2026, 5, 20, 2, 1, 0, tzinfo=UTC)
        ),
        metadata={"prompt": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT"},
        result={"content": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT"},
        error=error,
    )


def _runtime_event(
    event_id: str,
    run_id: str,
    *,
    event_kind: str,
    sequence: int,
) -> RuntimeEventRecord:
    return RuntimeEventRecord(
        event_id=event_id,
        run_id=run_id,
        event_kind=event_kind,
        payload={
            "message_type": event_kind,
            "content": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
            "tool_call_id": "tool-secret",
        },
        created_at=datetime(2026, 5, 20, 2, 0, sequence, tzinfo=UTC),
        sequence=sequence,
    )


async def _configure_run_detail_fixture(
    *,
    status: str = "completed",
    error: str | None = None,
) -> _ConsoleRuntimeStore:
    _register_console_session("session-detail")
    store = _ConsoleRuntimeStore(
        [
            _runtime_run(
                "run-detail",
                "session-detail",
                status=status,
                error=error,
            )
        ]
    )
    await store.save_message_snapshot(
        RunMessageSnapshotRecord(
            snapshot_id="run-detail:latest",
            run_id="run-detail",
            messages=[
                {
                    "role": "user",
                    "content": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
                },
                {"role": "assistant", "tool_calls": [{"id": "tool-secret"}]},
            ],
            metadata={
                "snapshot_kind": "latest_context",
                "prompt": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
            },
            created_at=datetime(2026, 5, 20, 2, 0, 10, tzinfo=UTC),
        )
    )
    await store.append_runtime_event(
        _runtime_event(
            "event-2",
            "run-detail",
            event_kind="wire.TurnEnd",
            sequence=2,
        )
    )
    await store.append_runtime_event(
        _runtime_event(
            "event-1",
            "run-detail",
            event_kind="wire.StreamDelta",
            sequence=1,
        )
    )
    session_manager.configure_runtime_store(store)
    return store


@pytest.mark.asyncio
async def test_console_shell_routes_render_navigation_without_secrets() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for route in CONSOLE_ROUTES:
            response = await client.get(
                route,
                params={"secret": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT"},
            )

            assert response.status_code == 200, route
            assert response.headers["content-type"].startswith("text/html")
            assert "<!doctype html>" in response.text.casefold()
            assert "Developer Console" in response.text
            for label, href in NAV_LINKS.items():
                assert label in response.text
                assert f'href="{href}"' in response.text
            for forbidden in FORBIDDEN_RENDERED_TEXT:
                assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_placeholder_pages_render_empty_states() -> None:
    expected = {
        "/console": "Console Overview",
        "/console/sessions": "Sessions",
        "/console/runs": "Runs",
        "/console/interactions": "HITL / Interactions",
        "/console/tape": "Tape",
        "/console/context": "Context",
        "/console/memory": "Memory",
        "/console/actions": "Actions / Validation",
        "/console/observability": "Observability",
        "/console/release": "Release / Health",
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for route, title in expected.items():
            response = await client.get(route)

            assert response.status_code == 200, route
            assert f"<h1>{title}</h1>" in response.text
            assert "No data loaded yet." in response.text


@pytest.mark.asyncio
async def test_console_sessions_list_renders_fixture_data_without_raw_content() -> None:
    _register_console_session("session-alpha")
    _register_console_session("session-running", status="running")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/sessions")

    assert response.status_code == 200
    assert "session-alpha" in response.text
    assert "session-running" in response.text
    assert "created" in response.text
    assert "running" in response.text
    assert "2026-05-20T01:02:03+00:00" in response.text
    assert "approval-secret-payload" not in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_run_detail_renders_completed_run_without_raw_snapshot() -> None:
    await _configure_run_detail_fixture()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/runs/run-detail")

    assert response.status_code == 200
    assert "Run Detail" in response.text
    assert "run-detail" in response.text
    assert "session-detail" in response.text
    assert "completed" in response.text
    assert "Message Snapshot" in response.text
    assert "2 messages" in response.text
    assert "role:user" in response.text
    assert "role:assistant" in response.text
    assert "Runtime Events" in response.text
    assert "wire.StreamDelta" in response.text
    assert "wire.TurnEnd" in response.text
    assert response.text.index("wire.StreamDelta") < response.text.index("wire.TurnEnd")
    assert "Last-Event-ID" in response.text
    assert "/runs/run-detail/events" in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text
    assert "tool-secret" not in response.text


@pytest.mark.asyncio
async def test_console_run_detail_renders_failed_run_with_safe_error_summary() -> None:
    await _configure_run_detail_fixture(status="failed", error="safe failure summary")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/runs/run-detail")

    assert response.status_code == 200
    assert "failed" in response.text
    assert "safe failure summary" in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_run_detail_renders_running_run_without_finished_time() -> None:
    await _configure_run_detail_fixture(status="running")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/runs/run-detail")

    assert response.status_code == 200
    assert "running" in response.text
    assert "<dt>Finished</dt><dd>-</dd>" in response.text


@pytest.mark.asyncio
async def test_console_run_detail_redacts_sensitive_error_summary() -> None:
    await _configure_run_detail_fixture(
        status="failed",
        error="SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT stdout",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/runs/run-detail")

    assert response.status_code == 200
    assert "Sensitive error summary redacted." in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_runs_list_renders_fixture_data_and_status_filter() -> None:
    _register_console_session("session-alpha")
    _register_console_session("session-beta")
    session_manager.configure_runtime_store(
        _ConsoleRuntimeStore(
            [
                _runtime_run("run-complete", "session-alpha", status="completed"),
                _runtime_run(
                    "run-failed",
                    "session-beta",
                    status="failed",
                    error="safe failure summary",
                ),
                _runtime_run("run-running", "session-alpha", status="running"),
            ]
        )
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/runs")
        failed_response = await client.get("/console/runs", params={"status": "failed"})

    assert response.status_code == 200
    assert "run-complete" in response.text
    assert "run-failed" in response.text
    assert "run-running" in response.text
    assert 'href="/console/runs/run-failed"' in response.text
    assert "safe failure summary" in response.text

    assert failed_response.status_code == 200
    assert "run-failed" in failed_response.text
    assert "run-complete" not in failed_response.text
    assert "run-running" not in failed_response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text
        assert forbidden not in failed_response.text


@pytest.mark.asyncio
async def test_console_runs_list_redacts_sensitive_error_summary() -> None:
    _register_console_session("session-sensitive")
    session_manager.configure_runtime_store(
        _ConsoleRuntimeStore(
            [
                _runtime_run(
                    "run-sensitive",
                    "session-sensitive",
                    status="failed",
                    error="SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT command_output",
                )
            ]
        )
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/runs")

    assert response.status_code == 200
    assert "run-sensitive" in response.text
    assert "Sensitive error summary redacted." in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text
