from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from agentkit.storage.protocols import TapeInfo, TapeSearchResult
from httpx import ASGITransport, AsyncClient

from coding_agent.runtime_store import (
    AgentInteractionRecord,
    AgentRunRecord,
    RunMessageSnapshotRecord,
    RuntimeEventRecord,
)
from coding_agent.core.config import settings
from coding_agent.ui import http_server
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


class _ConsoleTapeStore:
    def __init__(self, tape_ids: list[str] | None = None) -> None:
        self.tape_ids = tape_ids or ["tape-alpha"]

    async def save(self, tape_id: str, entries: list[dict[str, object]]) -> None:
        return None

    async def load(self, tape_id: str) -> list[dict[str, object]]:
        return []

    async def list_ids(self) -> list[str]:
        return list(self.tape_ids)

    async def truncate(self, tape_id: str, keep: int) -> None:
        return None

    async def info(self, tape_id: str) -> TapeInfo | None:
        if tape_id not in self.tape_ids:
            return None
        return TapeInfo(tape_id=tape_id, entry_count=3, first_seq=0, last_seq=2)

    async def search(
        self,
        *,
        tape_id: str | None = None,
        kind: str | None = None,
        run_id: str | None = None,
        tool_call_id: str | None = None,
        anchor_type: str | None = None,
        limit: int = 100,
    ) -> list[TapeSearchResult]:
        entries = []
        for known_tape_id in self.tape_ids:
            known_run_id = known_tape_id.replace("tape", "run")
            entries.extend(
                [
                    TapeSearchResult(
                        tape_id=known_tape_id,
                        seq=0,
                        entry={
                            "kind": "message",
                            "payload": {
                                "run_id": known_run_id,
                                "content": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
                            },
                        },
                    ),
                    TapeSearchResult(
                        tape_id=known_tape_id,
                        seq=1,
                        entry={
                            "kind": "tool_call",
                            "payload": {
                                "run_id": known_run_id,
                                "tool_call_id": "tool-alpha",
                            },
                        },
                    ),
                    TapeSearchResult(
                        tape_id=known_tape_id,
                        seq=2,
                        entry={
                            "kind": "anchor",
                            "meta": {"anchor_type": "handoff", "secret": "hidden"},
                        },
                    ),
                ]
            )
        filtered = []
        for result in entries:
            entry = result.entry
            payload = (
                entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
            )
            meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
            if tape_id is not None and result.tape_id != tape_id:
                continue
            if kind is not None and entry.get("kind") != kind:
                continue
            if (
                run_id is not None
                and payload.get("run_id") != run_id
                and meta.get("run_id") != run_id
            ):
                continue
            if (
                tool_call_id is not None
                and payload.get("tool_call_id") != tool_call_id
                and meta.get("tool_call_id") != tool_call_id
            ):
                continue
            if anchor_type is not None and meta.get("anchor_type") != anchor_type:
                continue
            filtered.append(result)
        if limit <= 0:
            return []
        return filtered[:limit]


class _ConsoleRuntimeStore:
    def __init__(self, runs: list[AgentRunRecord] | None = None) -> None:
        self.runs = {run.run_id: run for run in runs or []}
        self.events: dict[str, list[RuntimeEventRecord]] = {}
        self.snapshots: dict[str, RunMessageSnapshotRecord] = {}
        self.interactions: dict[str, AgentInteractionRecord] = {}

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
        self.interactions.setdefault(record.interaction_id, record)
        return record

    async def resolve_agent_interaction(
        self,
        interaction_id: str,
        *,
        status: str,
        response_payload: dict[str, object],
        resolved_at: datetime,
    ) -> AgentInteractionRecord:
        current = self.interactions[interaction_id]
        resolved = AgentInteractionRecord(
            interaction_id=current.interaction_id,
            run_id=current.run_id,
            interaction_kind=current.interaction_kind,
            status=status,
            request_payload=current.request_payload,
            response_payload=response_payload,
            metadata=current.metadata,
            created_at=current.created_at,
            resolved_at=resolved_at,
        )
        self.interactions[interaction_id] = resolved
        return resolved

    async def list_agent_interactions(
        self,
        run_id: str,
    ) -> list[AgentInteractionRecord]:
        return [
            interaction
            for interaction in self.interactions.values()
            if interaction.run_id == run_id
        ]


@pytest.fixture(autouse=True)
async def clear_console_state() -> AsyncIterator[None]:
    session_manager.configure_runtime_store(None)
    original_tape_store = session_manager._tape_store
    session_manager.clear_sessions()
    yield
    session_manager.configure_runtime_store(None)
    session_manager._tape_store = original_tape_store
    session_manager.clear_sessions()


def _register_console_session(
    session_id: str,
    *,
    status: str = "created",
    owner_label: str | None = None,
) -> Session:
    created_at = datetime(2026, 5, 20, 1, 2, 3, tzinfo=UTC)
    session = Session(
        id=session_id,
        created_at=created_at,
        last_activity=datetime(2026, 5, 20, 1, 3, 4, tzinfo=UTC),
        provider_name="fixture-provider",
        model_name="fixture-model",
        origin=None if owner_label is None else {"owner_label": owner_label},
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
    tape_id: str | None = None,
) -> AgentRunRecord:
    return AgentRunRecord(
        run_id=run_id,
        session_id=session_id,
        tape_id=tape_id or f"{session_id}-tape",
        parent_run_id=None,
        agent_id=None,
        status=status,
        started_at=datetime(2026, 5, 20, 2, 0, 0, tzinfo=UTC),
        ended_at=(
            None if status == "running" else datetime(2026, 5, 20, 2, 1, 0, tzinfo=UTC)
        ),
        metadata={
            "prompt": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
            "context_pack": {
                "sections": [
                    {
                        "title": "Repo references",
                        "items": [
                            {
                                "source_kind": "repo_file",
                                "source_id": "repo-src-auth",
                                "label": "Auth module",
                                "repo_path": "src/auth.py",
                                "line_start": 10,
                                "line_end": 20,
                                "score": 0.12,
                                "body": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
                                "evidence": [
                                    {
                                        "kind": "repo_file",
                                        "source_id": "repo-src-auth",
                                        "label": "reason: auth evidence",
                                        "repo_path": "src/auth.py",
                                        "line_start": 10,
                                        "line_end": 20,
                                        "chunk_id": "chunk-auth",
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "title": "Memory references",
                        "items": [
                            {
                                "source_kind": "memory",
                                "source_id": "mem-pack-ref",
                                "label": "Compacted memory prose that must not render",
                                "repo_path": "src/memory.py",
                                "line_start": 5,
                                "line_end": 6,
                                "evidence": [
                                    {
                                        "kind": "repo_file",
                                        "label": "memory evidence",
                                        "repo_path": "src/memory.py",
                                    }
                                ],
                            }
                        ],
                    },
                ]
            },
            "memory_evidence": [
                {
                    "source_id": "memory-auth-policy",
                    "summary": "Auth regression memory",
                    "label": "memory_auth_policy",
                    "status": "accepted",
                    "tags": ["src/auth.py", "tests/auth"],
                    "evidence": [
                        {
                            "repo_path": "src/auth.py",
                            "label": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
                        }
                    ],
                    "repo_path": "src/auth.py",
                    "line_start": 30,
                    "line_end": 32,
                }
            ],
            "actions": [
                {
                    "action_id": "action-alpha",
                    "kind": "patch",
                    "status": "completed",
                    "policy_decision": "allow",
                    "risk_level": "medium",
                    "changed_path_count": 2,
                    "file_extension_buckets": ".py,.md",
                    "approval_interaction_id": "interaction-pending",
                    "approval_status": "approved",
                    "validation_id": "validation-alpha",
                    "patch_summary": {
                        "hunk_count": 3,
                        "changed_path_count": 2,
                        "patch_content": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
                    },
                    "command": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
                }
            ],
            "retrieval_id": "retrieval-alpha",
            "validation_report": {
                "status": "failed",
                "outcomes": [
                    {
                        "label": "pytest_auth",
                        "status": "failed",
                        "exit_code": 1,
                        "duration_ms": 42,
                        "policy": {"decision": "allow"},
                        "failure_summary": {
                            "stdout_bytes": 12,
                            "stderr_bytes": 20,
                            "stdout_lines": 1,
                            "stderr_lines": 2,
                            "raw_output": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
                        },
                    }
                ],
            },
        },
        result={"content": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT"},
        error=error,
    )


def _owner_label(token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"owner:{digest}"


def _write_console_auth_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "server.toml"
    config_path.write_text(
        """
[agent]
name = "test-agent"
model = "test-model"
provider = "openai"

[server]
bearer_token = "user-token-a"
admin_bearer_token = "admin-token"
""".strip(),
        encoding="utf-8",
    )
    return config_path


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


def _interaction(
    interaction_id: str,
    run_id: str,
    *,
    status: str,
    resolved: bool = False,
) -> AgentInteractionRecord:
    created_at = datetime(2026, 5, 20, 2, 2, 0, tzinfo=UTC)
    return AgentInteractionRecord(
        interaction_id=interaction_id,
        run_id=run_id,
        interaction_kind="approval",
        status=status,
        request_payload={
            "tool_call": {"id": "tool-secret"},
            "prompt": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
        },
        response_payload={"content": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT"},
        metadata={
            "session_id": "session-alpha",
            "tool_call_id": "tool-call-visible",
            "tool_name": "bash_run",
            "secret": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
        },
        created_at=created_at,
        resolved_at=(datetime(2026, 5, 20, 2, 3, 0, tzinfo=UTC) if resolved else None),
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
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for route, title in expected.items():
            response = await client.get(route)

            assert response.status_code == 200, route
            assert f"<h1>{title}</h1>" in response.text
            assert "No data loaded yet." in response.text


@pytest.mark.asyncio
async def test_console_observability_renders_configured_links_and_correlation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_console_session("session-alpha")
    session_manager.configure_runtime_store(
        _ConsoleRuntimeStore(
            [_runtime_run("run-alpha", "session-alpha", status="completed")]
        )
    )
    monkeypatch.setattr(
        http_server,
        "_load_observability_config",
        lambda: {
            "enabled": True,
            "tracing": {
                "enabled": True,
                "backend": "langfuse",
                "public_url": "https://langfuse.example.test/project/demo",
                "public_key": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
            },
            "metrics": {
                "enabled": True,
                "endpoint_enabled": True,
                "backend": "prometheus",
                "grafana_url": "http://localhost:3000/d/coding-agent-observability",
                "token": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
            },
        },
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/console/observability",
            params={"run_id": "run-alpha"},
        )

    assert response.status_code == 200
    assert "Trace Correlation" in response.text
    assert "session-alpha" in response.text
    assert "run-alpha" in response.text
    assert "retrieval-alpha" in response.text
    assert "action-alpha" in response.text
    assert "validation-alpha" in response.text
    assert "interaction-pending" in response.text
    assert "langfuse" in response.text
    assert "prometheus" in response.text
    assert "https://langfuse.example.test/project/demo" in response.text
    assert "http://localhost:3000/d/coding-agent-observability" in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_observability_degrades_without_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        http_server,
        "_load_observability_config",
        lambda: {
            "enabled": True,
            "tracing": {"enabled": True, "backend": "otlp_http"},
            "metrics": {"enabled": False, "endpoint_enabled": False},
            "grafana_url": "https://grafana.example.test/?token=SECRET",
        },
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/observability")

    assert response.status_code == 200
    assert "Trace Correlation" in response.text
    assert "not configured" in response.text
    assert "otlp_http" in response.text
    assert "disabled at" in response.text
    assert "grafana.example.test" not in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_release_renders_health_and_release_manifest() -> None:
    _register_console_session("session-alpha")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/release")

    assert response.status_code == 200
    assert "Health / Readiness" in response.text
    assert "healthy" in response.text
    assert "session_store=ok" in response.text
    assert "rate_limiter=ok" in response.text
    assert "release-hardening-g38-g45" in response.text
    assert "durable-runtime-smoke" in response.text
    assert (
        "uv run pytest tests/integration/test_durable_runtime_smoke.py -v"
        in response.text
    )
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text


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
async def test_console_tape_renders_info_and_search_without_raw_payload() -> None:
    session_manager._tape_store = _ConsoleTapeStore()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/console/tape",
            params={"tape_id": "tape-alpha", "run_id": "run-alpha"},
        )

    assert response.status_code == 200
    assert "Tape Info" in response.text
    assert "tape-alpha" in response.text
    assert "3" in response.text
    assert "Tape Search" in response.text
    assert "tool_call" in response.text
    assert "tool-alpha" in response.text
    assert "message" in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_tape_store_fixture_honors_search_limit() -> None:
    store = _ConsoleTapeStore()

    results = await store.search(tape_id="tape-alpha", limit=1)

    assert len(results) == 1
    assert results[0].seq == 0


@pytest.mark.asyncio
async def test_console_tape_restricts_user_token_to_visible_tapes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CODING_AGENT_SERVER_CONFIG",
        str(_write_console_auth_config(tmp_path)),
    )
    monkeypatch.setattr(settings, "http_api_key", None)
    _register_console_session(
        "session-user",
        owner_label=_owner_label("user-token-a"),
    )
    _register_console_session(
        "session-admin",
        owner_label=_owner_label("admin-token"),
    )
    session_manager.configure_runtime_store(
        _ConsoleRuntimeStore(
            [
                _runtime_run(
                    "run-user",
                    "session-user",
                    status="completed",
                    tape_id="tape-user",
                ),
                _runtime_run(
                    "run-admin",
                    "session-admin",
                    status="completed",
                    tape_id="tape-admin",
                ),
            ]
        )
    )
    session_manager._tape_store = _ConsoleTapeStore(["tape-user", "tape-admin"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        forbidden = await client.get(
            "/console/tape",
            params={"tape_id": "tape-admin"},
            headers={"Authorization": "Bearer user-token-a"},
        )
        allowed = await client.get(
            "/console/tape",
            params={"tape_id": "tape-user"},
            headers={"Authorization": "Bearer user-token-a"},
        )
        admin = await client.get(
            "/console/tape",
            params={"tape_id": "tape-admin"},
            headers={"Authorization": "Bearer admin-token"},
        )

    assert forbidden.status_code == 200
    assert "tape-admin" not in forbidden.text
    assert "No tape info is available." in forbidden.text
    assert allowed.status_code == 200
    assert "tape-user" in allowed.text
    assert admin.status_code == 200
    assert "tape-admin" in admin.text


@pytest.mark.asyncio
async def test_console_tape_renders_missing_state() -> None:
    session_manager._tape_store = _ConsoleTapeStore()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/tape", params={"tape_id": "missing"})

    assert response.status_code == 200
    assert "Tape Info" in response.text
    assert "No tape info is available." in response.text


@pytest.mark.asyncio
async def test_console_context_renders_context_pack_evidence_without_body() -> None:
    _register_console_session("session-alpha")
    session_manager.configure_runtime_store(
        _ConsoleRuntimeStore(
            [_runtime_run("run-alpha", "session-alpha", status="completed")]
        )
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/context", params={"run_id": "run-alpha"})

    assert response.status_code == 200
    assert "Context Inspector" in response.text
    assert "Repo references" in response.text
    assert "Auth module" in response.text
    assert "repo_file" in response.text
    assert "src/auth.py" in response.text
    assert "10-20" in response.text
    assert "0.12" in response.text
    assert "reason: auth evidence" in response.text
    assert 'href="/console/runs/run-alpha"' in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_context_renders_empty_state_for_missing_pack() -> None:
    _register_console_session("session-alpha")
    run = _runtime_run("run-alpha", "session-alpha", status="completed")
    session_manager.configure_runtime_store(
        _ConsoleRuntimeStore(
            [
                AgentRunRecord(
                    run_id=run.run_id,
                    session_id=run.session_id,
                    tape_id=run.tape_id,
                    parent_run_id=run.parent_run_id,
                    agent_id=run.agent_id,
                    status=run.status,
                    started_at=run.started_at,
                    ended_at=run.ended_at,
                    metadata={},
                    result=run.result,
                    error=run.error,
                )
            ]
        )
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/context", params={"run_id": "run-alpha"})

    assert response.status_code == 200
    assert "Context Inspector" in response.text
    assert "No context pack evidence is available." in response.text


@pytest.mark.asyncio
async def test_console_memory_renders_memory_evidence_without_raw_content() -> None:
    _register_console_session("session-alpha")
    session_manager.configure_runtime_store(
        _ConsoleRuntimeStore(
            [_runtime_run("run-alpha", "session-alpha", status="completed")]
        )
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/memory", params={"run_id": "run-alpha"})

    assert response.status_code == 200
    assert "Memory Evidence" in response.text
    assert "memory-auth-policy" in response.text
    assert "mem-pack-ref" in response.text
    assert "memory_auth_policy" in response.text
    assert "Compacted memory prose that must not render" not in response.text
    assert "Auth regression memory" not in response.text
    assert "accepted" in response.text
    assert "src/auth.py" in response.text
    assert "30-32" in response.text
    assert 'href="/console/runs/run-alpha"' in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_memory_renders_empty_state_for_missing_run() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/memory", params={"run_id": "missing"})

    assert response.status_code == 200
    assert "Memory Evidence" in response.text
    assert "No memory evidence is available." in response.text


@pytest.mark.asyncio
async def test_console_actions_renders_action_validation_and_policy_summaries() -> None:
    _register_console_session("session-alpha")
    session_manager.configure_runtime_store(
        _ConsoleRuntimeStore(
            [_runtime_run("run-alpha", "session-alpha", status="completed")]
        )
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/actions", params={"run_id": "run-alpha"})

    assert response.status_code == 200
    assert "Action Executions" in response.text
    assert "action-alpha" in response.text
    assert "patch" in response.text
    assert "allow" in response.text
    assert "medium" in response.text
    assert ".py, .md" in response.text
    assert "interaction-pending" in response.text
    assert "validation-alpha" in response.text
    assert "hunk_count=3" in response.text
    assert "Validation Results" in response.text
    assert "pytest_auth" in response.text
    assert "failed" in response.text
    assert "output_bytes=12" in response.text
    assert "error_lines=2" in response.text
    assert 'href="/console/context?run_id=run-alpha"' in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_actions_renders_empty_state_without_run_id() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/actions")

    assert response.status_code == 200
    assert "Action Executions" in response.text
    assert "Validation Results" in response.text
    assert "No action summaries are available." in response.text
    assert "No validation summaries are available." in response.text


@pytest.mark.asyncio
async def test_console_memory_and_actions_restrict_user_token_to_visible_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CODING_AGENT_SERVER_CONFIG",
        str(_write_console_auth_config(tmp_path)),
    )
    monkeypatch.setattr(settings, "http_api_key", None)
    _register_console_session(
        "session-user",
        owner_label=_owner_label("user-token-a"),
    )
    _register_console_session(
        "session-admin",
        owner_label=_owner_label("admin-token"),
    )
    session_manager.configure_runtime_store(
        _ConsoleRuntimeStore(
            [
                _runtime_run("run-user", "session-user", status="completed"),
                _runtime_run("run-admin", "session-admin", status="completed"),
            ]
        )
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        memory = await client.get(
            "/console/memory",
            params={"run_id": "run-admin"},
            headers={"Authorization": "Bearer user-token-a"},
        )
        actions = await client.get(
            "/console/actions",
            params={"run_id": "run-admin"},
            headers={"Authorization": "Bearer user-token-a"},
        )

    assert memory.status_code == 200
    assert "memory-auth-policy" not in memory.text
    assert "No memory evidence is available." in memory.text
    assert actions.status_code == 200
    assert "action-alpha" not in actions.text
    assert "No action summaries are available." in actions.text


@pytest.mark.asyncio
async def test_console_interactions_renders_pending_and_resolved_lists() -> None:
    _register_console_session("session-alpha")
    store = _ConsoleRuntimeStore(
        [_runtime_run("run-alpha", "session-alpha", status="running")]
    )
    await store.create_agent_interaction(
        _interaction("interaction-pending", "run-alpha", status="pending")
    )
    await store.create_agent_interaction(
        _interaction(
            "interaction-approved",
            "run-alpha",
            status="approved",
            resolved=True,
        )
    )
    session_manager.configure_runtime_store(store)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/interactions")

    assert response.status_code == 200
    assert "Pending Interactions" in response.text
    assert "Resolved Interactions" in response.text
    assert "interaction-pending" in response.text
    assert "interaction-approved" in response.text
    assert "run-alpha" in response.text
    assert "session-alpha" in response.text
    assert "approval" in response.text
    assert "pending" in response.text
    assert "approved" in response.text
    assert "tool-call-visible" in response.text
    assert 'href="/console/runs/run-alpha"' in response.text
    assert "tool-secret" not in response.text
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_interactions_displays_terminal_duplicate_state_safely() -> None:
    _register_console_session("session-alpha")
    store = _ConsoleRuntimeStore(
        [_runtime_run("run-alpha", "session-alpha", status="completed")]
    )
    await store.create_agent_interaction(
        _interaction(
            "interaction-terminal",
            "run-alpha",
            status="duplicate_terminal",
            resolved=True,
        )
    )
    session_manager.configure_runtime_store(store)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/interactions")

    assert response.status_code == 200
    assert "interaction-terminal" in response.text
    assert "duplicate_terminal" in response.text
    assert "Resolved Interactions" in response.text
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
    assert "last_event_id" in response.text
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
async def test_console_run_detail_redacts_sensitive_message_labels() -> None:
    store = await _configure_run_detail_fixture()
    await store.save_message_snapshot(
        RunMessageSnapshotRecord(
            snapshot_id="run-detail:latest",
            run_id="run-detail",
            messages=[
                {"role": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT"},
                {"message_type": "command_output"},
            ],
            metadata={},
            created_at=datetime(2026, 5, 20, 2, 0, 10, tzinfo=UTC),
        )
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/console/runs/run-detail")

    assert response.status_code == 200
    assert "<li>message</li>" in response.text
    assert "role:SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT" not in response.text
    assert "type:command_output" not in response.text
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
