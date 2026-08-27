from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from coding_agent.events.connected_chat import (
    CHAT_EVENT_KINDS,
    CONNECTED_CHAT_CONTRACT_VERSION,
    CONNECTED_CHAT_PROJECTION,
    ChatCursorError,
    ChatEvent,
    ConnectedChatCursor,
    build_chat_admission,
    decode_chat_cursor,
    encode_chat_cursor,
)
from coding_agent.server.schemas import ConnectedChatEventSchema
from coding_agent.stores.runtime_store import SessionFactSourceState


FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "connected_chat"
    / "v1"
    / "connected-chat-contract.json"
)


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _state(
    *,
    session_id: str = "session-01",
    projection: str = "connected-chat",
    epoch: str = "7",
    session_seq: str = "20",
    retention_floor: str = "0",
) -> SessionFactSourceState:
    return SessionFactSourceState(
        session_id=session_id,
        session_seq=session_seq,
        retention_floor=retention_floor,
        projection=projection,
        projection_epoch=epoch,
    )


def _canonical_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def test_fixture_covers_complete_connected_chat_contract() -> None:
    fixture = _fixture()

    assert fixture["contract_id"] == "cal.connected-chat"
    assert fixture["contract_version"] == CONNECTED_CHAT_CONTRACT_VERSION
    assert fixture["projection"] == {"name": CONNECTED_CHAT_PROJECTION, "epoch": "7"}
    assert fixture["identity"] == {
        "dedupe_key": "source_event_id",
        "sequence_type": "decimal-string",
        "delivery": "at-least-once",
    }

    events = fixture["events"]
    assert {event["data"]["kind"] for event in events} == set(CHAT_EVENT_KINDS)
    assert {
        event["data"]["payload"]["outcome"]
        for event in events
        if event["data"]["kind"] == "root_terminal"
    } == {
        "completed",
        "failed",
        "cancelled",
        "interrupted",
    }
    for event in events:
        data = event["data"]
        assert event["event"] == "chat_event"
        assert event["id"] == data["session_seq"]
        assert data["contract_version"] == CONNECTED_CHAT_CONTRACT_VERSION
        assert data["source_event_id"]
        assert data["session_id"]
        assert data["kind"] in CHAT_EVENT_KINDS
        assert isinstance(data["payload"], dict)
        assert data["session_seq"] == str(int(data["session_seq"]))

    assert {
        (error["status"], error["reason"], error["replay_required"])
        for error in fixture["cursor"]["errors"]
    } == {
        (400, "cursor_malformed", False),
        (409, "cursor_foreign_session", False),
        (410, "cursor_expired", True),
        (409, "cursor_wrong_epoch", True),
        (409, "cursor_future", False),
    }
    assert {control["data"]["reason"] for control in fixture["stream_controls"]} == {
        "subscriber_queue_overflow",
        "ownership_lost",
        "sequence_loss",
    }


def test_cursor_fixture_bytes_round_trip_canonically() -> None:
    fixture = _fixture()
    state = _state()

    for example in fixture["cursor"]["examples"]:
        encoded = example["encoded"]
        decoded = decode_chat_cursor(
            encoded,
            expected_session_id="session-01",
            fact_state=state,
        )
        assert decoded == ConnectedChatCursor(**example["payload"])
        assert encode_chat_cursor(decoded) == encoded
        assert _canonical_cursor(example["payload"]) == encoded


@pytest.mark.parametrize(
    (
        "case",
        "value",
        "expected_session_id",
        "state",
        "status",
        "code",
        "replay_required",
    ),
    [
        (
            "malformed",
            "not-base64!",
            "session-01",
            _state(),
            400,
            "cursor_malformed",
            False,
        ),
        (
            "noncanonical",
            base64.urlsafe_b64encode(b'{"v":1, "kind":"chat"}')
            .decode("ascii")
            .rstrip("="),
            "session-01",
            _state(),
            400,
            "cursor_malformed",
            False,
        ),
        (
            "foreign",
            _canonical_cursor(
                {
                    "v": 1,
                    "kind": "chat",
                    "session_id": "session-02",
                    "projection": "connected-chat",
                    "epoch": "7",
                    "after_seq": "12",
                    "high_water_seq": "20",
                }
            ),
            "session-01",
            _state(),
            409,
            "cursor_foreign_session",
            False,
        ),
        (
            "expired",
            _canonical_cursor(
                {
                    "v": 1,
                    "kind": "chat",
                    "session_id": "session-01",
                    "projection": "connected-chat",
                    "epoch": "7",
                    "after_seq": "2",
                    "high_water_seq": "20",
                }
            ),
            "session-01",
            _state(retention_floor="4"),
            410,
            "cursor_expired",
            True,
        ),
        (
            "wrong_projection",
            _canonical_cursor(
                {
                    "v": 1,
                    "kind": "chat",
                    "session_id": "session-01",
                    "projection": "other",
                    "epoch": "7",
                    "after_seq": "12",
                    "high_water_seq": "20",
                }
            ),
            "session-01",
            _state(),
            409,
            "cursor_wrong_epoch",
            True,
        ),
        (
            "wrong_epoch",
            _canonical_cursor(
                {
                    "v": 1,
                    "kind": "chat",
                    "session_id": "session-01",
                    "projection": "connected-chat",
                    "epoch": "6",
                    "after_seq": "12",
                    "high_water_seq": "20",
                }
            ),
            "session-01",
            _state(),
            409,
            "cursor_wrong_epoch",
            True,
        ),
        (
            "future_after",
            _canonical_cursor(
                {
                    "v": 1,
                    "kind": "chat",
                    "session_id": "session-01",
                    "projection": "connected-chat",
                    "epoch": "7",
                    "after_seq": "21",
                    "high_water_seq": "21",
                }
            ),
            "session-01",
            _state(),
            409,
            "cursor_future",
            False,
        ),
        (
            "future_high_water",
            _canonical_cursor(
                {
                    "v": 1,
                    "kind": "chat",
                    "session_id": "session-01",
                    "projection": "connected-chat",
                    "epoch": "7",
                    "after_seq": "12",
                    "high_water_seq": "21",
                }
            ),
            "session-01",
            _state(),
            409,
            "cursor_future",
            False,
        ),
    ],
)
def test_cursor_error_taxonomy(
    case: str,
    value: str,
    expected_session_id: str,
    state: SessionFactSourceState,
    status: int,
    code: str,
    replay_required: bool,
) -> None:
    with pytest.raises(ChatCursorError) as caught:
        decode_chat_cursor(
            value,
            expected_session_id=expected_session_id,
            fact_state=state,
        )

    assert caught.value.status == status, case
    assert caught.value.code == code, case
    assert caught.value.replay_required is replay_required, case


def test_deterministic_run_ids_use_unambiguous_structured_input() -> None:
    state = {"id": "a:b", "session_id": "a:b", "tape_id": "tape"}
    _, delimiter_left = build_chat_admission(
        session_id="a:b",
        prompt="prompt",
        command_id="c",
        parent_run_id=None,
        session_state=state,
    )
    _, delimiter_right = build_chat_admission(
        session_id="a",
        prompt="prompt",
        command_id="b:c",
        parent_run_id=None,
        session_state={"id": "a", "session_id": "a", "tape_id": "tape"},
    )
    _, unicode_left = build_chat_admission(
        session_id="会話:一",
        prompt="prompt",
        command_id="命令",
        parent_run_id=None,
        session_state={"id": "会話:一", "session_id": "会話:一", "tape_id": "tape"},
    )
    _, unicode_right = build_chat_admission(
        session_id="会話",
        prompt="prompt",
        command_id="一:命令",
        parent_run_id=None,
        session_state={"id": "会話", "session_id": "会話", "tape_id": "tape"},
    )

    assert delimiter_left.run_id != delimiter_right.run_id
    assert unicode_left.run_id != unicode_right.run_id


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("user_prompt", {}),
        ("assistant_message", {"text": 3}),
        ("thinking", {"text": None}),
        ("progress", {"current": 3, "total": 2, "label": "bad"}),
        ("tool_call", {"tool_name": "bash", "arguments": {}}),
        ("tool_result", {"output": "missing call", "is_error": False}),
        ("root_terminal", {"outcome": "unknown", "result": None, "error": None}),
    ],
)
def test_connected_chat_schema_rejects_malformed_payloads(
    kind: str, payload: dict[str, object]
) -> None:
    with pytest.raises(ValueError):
        ConnectedChatEventSchema.model_validate(
            {
                "contract_version": "1.0.0",
                "source_event_id": "event-1",
                "session_seq": "1",
                "session_id": "session-1",
                "run_id": "run-1",
                "kind": kind,
                "created_at": "2026-08-24T00:00:00Z",
                "payload": payload,
            }
        )


def test_chat_event_constructor_rejects_malformed_typed_payload() -> None:
    from datetime import UTC, datetime

    with pytest.raises(ValueError):
        ChatEvent(
            source_event_id="event-1",
            session_seq="1",
            session_id="session-1",
            run_id="run-1",
            kind="tool_result",
            created_at=datetime(2026, 8, 24, tzinfo=UTC),
            payload={"output": "missing call", "is_error": False},
        )


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("user_prompt", {"text": "prompt"}),
        ("assistant_message", {"text": "answer"}),
        ("thinking", {"text": "reasoning"}),
        ("progress", {"current": 1, "total": 2, "label": "working"}),
        (
            "tool_call",
            {"call_id": "call-1", "tool_name": "bash", "arguments": {"cmd": "pwd"}},
        ),
        (
            "tool_result",
            {"call_id": "call-1", "output": "/repo", "is_error": False},
        ),
        (
            "root_terminal",
            {
                "outcome": "failed",
                "result": None,
                "error": {"code": "boom", "message": "failed"},
            },
        ),
    ],
)
def test_connected_chat_schema_json_preserves_exact_typed_payload(
    kind: str, payload: dict[str, object]
) -> None:
    event = ConnectedChatEventSchema.model_validate(
        {
            "contract_version": "1.0.0",
            "source_event_id": "event-1",
            "session_seq": "1",
            "session_id": "session-1",
            "run_id": "run-1",
            "kind": kind,
            "created_at": "2026-08-24T00:00:00Z",
            "payload": payload,
        }
    )

    assert json.loads(event.model_dump_json())["payload"] == payload


def test_connected_chat_schema_allows_deliberate_additive_payload_fields() -> None:
    event = ConnectedChatEventSchema.model_validate(
        {
            "contract_version": "1.0.0",
            "source_event_id": "event-1",
            "session_seq": "1",
            "session_id": "session-1",
            "run_id": "run-1",
            "kind": "tool_result",
            "created_at": "2026-08-24T00:00:00Z",
            "payload": {
                "call_id": "call-1",
                "output": "ok",
                "is_error": False,
                "vendor_detail": {"duration_ms": 1},
            },
        }
    )

    assert event.payload.vendor_detail == {"duration_ms": 1}
