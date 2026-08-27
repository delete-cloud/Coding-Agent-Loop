"""Fixture-driven HTTP/OpenAPI contract tests for connected chat."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from coding_agent.events.connected_chat import (
    ChatCommandAdmission,
    ChatCursorError,
    ChatEvent,
    ChatCommandConflictError,
    ResumeSourceUnsettledError,
    TurnInProgressError,
)
from coding_agent.runs.resume import DEFAULT_RESUME_PROMPT
from coding_agent.server.http.routes import sessions as session_routes
from coding_agent.server.http_server import app, session_manager
from coding_agent.server.rate_limit import limiter
from coding_agent.server.session_manager import Session

FIXTURE = json.loads(
    (
        Path(__file__).parents[1]
        / "fixtures/connected_chat/v1/connected-chat-contract.json"
    ).read_text(encoding="utf-8")
)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    limiter.reset()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as value:
        yield value


@pytest.fixture
async def registered_session(isolated_http_session_manager) -> Session:
    now = datetime.now(UTC)
    session = Session(id="session-01", created_at=now, last_activity=now)
    isolated_http_session_manager.register_session(session)
    return session


def _fixture_event(index: int = 0) -> ChatEvent:
    data = FIXTURE["events"][index]["data"]
    return ChatEvent(
        source_event_id=data["source_event_id"],
        session_seq=data["session_seq"],
        session_id=data["session_id"],
        run_id=data["run_id"],
        kind=data["kind"],
        created_at=__import__("datetime").datetime.fromisoformat(
            data["created_at"].replace("Z", "+00:00")
        ),
        payload=data["payload"],
    )


async def test_snapshot_and_cursor_errors_match_fixture(
    client: AsyncClient, registered_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = FIXTURE["http"]["snapshot"]["response"]
    snapshot = SimpleNamespace(**{**expected, "events": ()})
    monkeypatch.setattr(
        session_manager,
        "snapshot_chat_events",
        AsyncMock(return_value=snapshot),
        raising=False,
    )

    response = await client.get("/sessions/session-01/chat-events?limit=2")
    assert response.status_code == 200
    assert response.json() == expected

    for cursor_error in FIXTURE["cursor"]["errors"]:
        monkeypatch.setattr(
            session_manager,
            "snapshot_chat_events",
            AsyncMock(
                side_effect=ChatCursorError(
                    cursor_error["reason"],
                    status=cursor_error["status"],
                    replay_required=cursor_error["replay_required"],
                )
            ),
            raising=False,
        )
        response = await client.get("/sessions/session-01/chat-events?cursor=bad")
        assert response.status_code == cursor_error["status"]
        body = response.json()
        assert body["error"]["code"] == cursor_error["reason"]
        assert body["error"]["retryable"] is False
        assert (
            body["error"].get("replay_required", False)
            is cursor_error["replay_required"]
        )


async def test_root_terminal_payload_survives_snapshot_and_sse_serialization(
    client: AsyncClient, registered_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    event = _fixture_event(7)
    snapshot = SimpleNamespace(
        contract_version="1.0.0",
        session_id="session-01",
        projection="connected-chat",
        projection_epoch="1",
        snapshot_cursor="cursor-1",
        next_cursor=None,
        events=(event,),
    )
    monkeypatch.setattr(
        session_manager,
        "snapshot_chat_events",
        AsyncMock(return_value=snapshot),
        raising=False,
    )

    async def stream(*args, **kwargs):
        del args, kwargs
        yield event

    monkeypatch.setattr(session_manager, "follow_chat_events", stream, raising=False)

    snapshot_response = await client.get("/sessions/session-01/chat-events")
    assert snapshot_response.status_code == 200
    assert snapshot_response.json()["events"][0]["payload"] == event.payload

    follow_response = await client.get("/sessions/session-01/chat-events/follow")
    assert follow_response.status_code == 200
    sse_payload = json.loads(follow_response.text.split("data: ", 1)[1].splitlines()[0])
    assert sse_payload["payload"] == event.payload


async def test_prompt_resume_follow_and_cancel_contracts(
    client: AsyncClient, registered_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    event = _fixture_event()

    async def stream(*args, **kwargs):
        del args, kwargs
        yield event

    monkeypatch.setattr(session_manager, "stream_chat_command", stream, raising=False)
    monkeypatch.setattr(session_manager, "follow_chat_events", stream, raising=False)
    monkeypatch.setattr(
        session_manager,
        "admit_chat_command",
        AsyncMock(
            return_value=ChatCommandAdmission("session-01", "run-01", "cmd-01", None)
        ),
    )
    monkeypatch.setattr(
        session_manager,
        "cancel_session_turn",
        AsyncMock(
            return_value=SimpleNamespace(
                session_id="session-01", turn_id="run-01", status="cancelling"
            )
        ),
    )

    prompt = await client.post(
        "/sessions/session-01/prompt",
        json=FIXTURE["http"]["prompt"]["request"],
    )
    assert prompt.status_code == 200
    session_manager.admit_chat_command.assert_awaited_once_with(
        "session-01", prompt="Run tests", command_id="cmd-01"
    )
    assert prompt.headers["content-type"].startswith("text/event-stream")
    assert "event: chat_event" in prompt.text
    assert f"id: {event.session_seq}" in prompt.text
    assert (
        json.loads(prompt.text.split("data: ", 1)[1].splitlines()[0])["source_event_id"]
        == event.source_event_id
    )

    resume_admission = replace(
        ChatCommandAdmission("session-01", "run-02", "cmd-02", "run-01")
    )
    session_manager.admit_chat_command.return_value = resume_admission
    resume = await client.post(
        "/sessions/session-01/resume",
        json=FIXTURE["http"]["resume"]["request"],
    )
    assert resume.status_code == 200
    session_manager.admit_chat_command.assert_awaited_with(
        "session-01",
        prompt=DEFAULT_RESUME_PROMPT,
        command_id="cmd-02",
        parent_run_id="run-01",
    )
    assert "event: chat_event" in resume.text

    follow = await client.get(
        "/sessions/session-01/chat-events/follow",
        params={"cursor": FIXTURE["http"]["follow"]["cursor"]},
    )
    assert follow.status_code == 200
    assert follow.headers["content-type"].startswith("text/event-stream")
    assert "event: chat_event" in follow.text

    cancelled = await client.post("/sessions/session-01/cancel")
    assert cancelled.status_code == 202
    assert cancelled.json() == FIXTURE["http"]["cancel"]["response"]


async def test_pm0023_passive_get_cleanup_unregisters_once_without_settlement(
    registered_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del registered_session
    stream_finally = 0
    unregister_calls = 0
    settle_root_run = AsyncMock()

    class FakeEventSourceResponse:
        def __init__(self, body_iterator, **kwargs: object) -> None:
            del kwargs
            self.body_iterator = body_iterator

    async def unregister() -> None:
        nonlocal unregister_calls
        unregister_calls += 1

    async def follow_chat_events(
        current_session_id: str, *, cursor: str | None
    ) -> AsyncIterator[ChatEvent]:
        nonlocal stream_finally
        assert current_session_id == "session-01"
        assert cursor == "3"
        try:
            yield _fixture_event()
            await asyncio.Event().wait()
        finally:
            stream_finally += 1
            await unregister()

    monkeypatch.setattr(session_routes, "EventSourceResponse", FakeEventSourceResponse)
    monkeypatch.setattr(
        session_manager, "follow_chat_events", follow_chat_events, raising=False
    )
    monkeypatch.setattr(
        session_manager, "settle_root_run", settle_root_run, raising=False
    )

    response = await session_routes.follow_chat_events(
        Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/sessions/session-01/chat-events/follow",
                "headers": [],
            }
        ),
        "session-01",
        cursor="3",
        x_api_key=None,
        authorization=None,
    )
    event_generator = response.body_iterator
    assert (await anext(event_generator))["event"] == "chat_event"

    await event_generator.aclose()

    assert unregister_calls == 1
    assert stream_finally == 1
    settle_root_run.assert_not_awaited()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TurnInProgressError(), FIXTURE["http"]["errors"]["admission"][0]),
        (ChatCommandConflictError(), FIXTURE["http"]["errors"]["admission"][1]),
        (ResumeSourceUnsettledError(), FIXTURE["http"]["errors"]["lifecycle"][0]),
    ],
)
async def test_checked_admission_errors_use_exact_envelopes(
    client: AsyncClient,
    registered_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected: dict[str, object],
) -> None:
    monkeypatch.setattr(
        session_manager, "admit_chat_command", AsyncMock(side_effect=error)
    )
    response = await client.post(
        "/sessions/session-01/prompt?event_format=display",
        json=FIXTURE["http"]["prompt"]["request"],
    )
    assert response.status_code == expected["status"]
    assert response.json() == expected["body"]


async def test_no_active_and_not_found_are_checked(
    client: AsyncClient, registered_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        session_manager,
        "cancel_session_turn",
        AsyncMock(
            return_value=SimpleNamespace(
                session_id="session-01", turn_id=None, status="idle"
            )
        ),
    )
    response = await client.post("/sessions/session-01/cancel")
    assert response.status_code == 409
    assert response.json() == FIXTURE["http"]["errors"]["lifecycle"][1]["body"]

    missing = await client.get("/sessions/missing/chat-events")
    assert missing.status_code == 404
    assert missing.json() == {
        "error": {
            "code": "session_not_found",
            "message": "Session not found",
            "retryable": False,
        }
    }


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/sessions", None),
        ("POST", "/sessions", {}),
        ("GET", "/sessions/session-01/chat-events", None),
        ("GET", "/sessions/session-01/chat-events/follow", None),
        ("POST", "/sessions/session-01/prompt", FIXTURE["http"]["prompt"]["request"]),
        ("POST", "/sessions/session-01/resume", FIXTURE["http"]["resume"]["request"]),
        ("POST", "/sessions/session-01/cancel", None),
    ],
)
async def test_enabled_auth_missing_credentials_has_fixture_envelope(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    from coding_agent.core.config import settings

    monkeypatch.setattr(settings, "http_api_key", "secret")
    response = await client.request(method, path, json=body)
    expected = FIXTURE["http"]["errors"]["auth"][0]
    assert response.status_code == expected["status"]
    assert response.json() == expected["body"]


async def test_connected_resume_requires_parent_before_admission(
    client: AsyncClient,
    registered_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del registered_session
    admission = AsyncMock(
        return_value=ChatCommandAdmission(
            "session-01", "run-resume", "cmd-resume", None
        )
    )

    async def stream(*args: object, **kwargs: object) -> AsyncIterator[ChatEvent]:
        del args, kwargs
        yield _fixture_event()

    monkeypatch.setattr(session_manager, "admit_chat_command", admission)
    monkeypatch.setattr(session_manager, "stream_chat_command", stream)

    response = await client.post(
        "/sessions/session-01/resume",
        json={"command_id": "cmd-resume", "prompt": None},
    )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "parent_run_id_required",
            "message": "Connected Resume requires parent_run_id",
            "retryable": False,
        }
    }
    admission.assert_not_awaited()


async def test_openapi_freezes_statuses_examples_and_sse_media() -> None:
    document = app.openapi()
    expected_statuses = {
        "/sessions/{session_id}/chat-events": {
            "200",
            "400",
            "401",
            "404",
            "409",
            "410",
            "422",
        },
        "/sessions/{session_id}/chat-events/follow": {
            "200",
            "400",
            "401",
            "404",
            "409",
            "410",
            "422",
        },
        "/sessions/{session_id}/prompt": {"200", "401", "404", "409", "422"},
        "/sessions/{session_id}/resume": {"200", "401", "404", "409", "422"},
        "/sessions/{session_id}/cancel": {"202", "401", "404", "409", "422"},
    }
    for path, statuses in expected_statuses.items():
        operation = next(iter(document["paths"][path].values()))
        assert statuses <= set(operation["responses"])
    fixture_event = FIXTURE["events"][0]["data"]
    fixture_control = FIXTURE["stream_controls"][0]["data"]
    for path, method in (
        ("/sessions/{session_id}/chat-events/follow", "get"),
        ("/sessions/{session_id}/prompt", "post"),
        ("/sessions/{session_id}/resume", "post"),
    ):
        sse = document["paths"][path][method]["responses"]["200"]["content"][
            "text/event-stream"
        ]
        assert sse != {}
        assert sse["schema"]["oneOf"] == [
            {"$ref": "#/components/schemas/ConnectedChatEventSchema"},
            {"$ref": "#/components/schemas/ConnectedChatStreamControlSchema"},
        ]
        assert sse["examples"]["chat_event"]["value"] == fixture_event
        assert sse["examples"]["stream_control"]["value"] == fixture_control
    event_schema = document["components"]["schemas"]["ConnectedChatEventSchema"]
    payload_schema = event_schema["properties"]["payload"]
    payload_refs = {item["$ref"].rsplit("/", 1)[-1] for item in payload_schema["anyOf"]}
    referenced_payload_fields = {
        frozenset(document["components"]["schemas"][ref]["properties"])
        for ref in payload_refs
    }
    assert {
        frozenset({"text"}),
        frozenset({"current", "total", "label"}),
        frozenset({"call_id", "tool_name", "arguments"}),
        frozenset({"call_id", "output", "is_error"}),
        frozenset({"outcome", "result", "error"}),
    } <= referenced_payload_fields
    assert "/openrpc" not in document["paths"]
