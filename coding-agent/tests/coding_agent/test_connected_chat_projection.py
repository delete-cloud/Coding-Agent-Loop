from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from coding_agent.events.connected_chat import (
    ChatCursorError,
    ChatProjectionCorruptionError,
    decode_chat_cursor,
    project_chat_event,
)
from coding_agent.stores.runtime_store import (
    AgentRunRecord,
    AuthoritativeUnitOfWork,
    EventRecord,
)
from tests.coding_agent.test_harness_p2_fact_source import (
    SESSION_ID,
    SESSION_PAYLOAD,
    TAPE_ID,
    _open_store,
    _restore,
)


def _run(run_id: str, *, superseded: bool = False) -> AgentRunRecord:
    return AgentRunRecord(
        run_id=run_id,
        session_id=SESSION_ID,
        tape_id=TAPE_ID,
        parent_run_id=None,
        agent_id=None,
        status="running",
        started_at=datetime(2026, 8, 24, 0, 0, tzinfo=UTC),
        metadata={},
        result={},
        superseded_by_checkpoint_id="checkpoint-old" if superseded else None,
        superseded_at=datetime(2026, 8, 24, 0, 1, tzinfo=UTC) if superseded else None,
    )


async def _append_chat(
    store: Any,
    owner: Any,
    suffix: str,
    *,
    run_id: str | None = "run-active",
    run_state: AgentRunRecord | None = None,
) -> None:
    payload: dict[str, object] = {"text": f"message-{suffix}"}
    if run_id is not None:
        payload["run_id"] = run_id
    await store.commit_authoritative_uow(
        owner,
        AuthoritativeUnitOfWork(
            event=EventRecord(
                event_id=f"chat-{suffix}",
                session_id=SESSION_ID,
                event_kind="assistant_message",
                payload=payload,
                created_at=datetime(2026, 8, 24, 0, 0, tzinfo=UTC),
            ),
            session_state={**SESSION_PAYLOAD, "turn": suffix},
            run_state=run_state,
        ),
    )


@pytest.fixture(params=["sqlite", "pg"])
def store_kind(request: pytest.FixtureRequest) -> str:
    return str(request.param)


def test_connected_chat_projector_filters_child_internals_and_keeps_targeted_approval() -> (
    None
):
    parent = _run("parent-run")
    internal = EventRecord(
        event_id="child-internal",
        session_id=SESSION_ID,
        event_kind="assistant_message",
        payload={
            "run_id": "child-run",
            "text": "private child thought",
            "subagent_child": True,
            "skip_parent_context": True,
        },
        created_at=datetime(2026, 8, 24, tzinfo=UTC),
        session_seq="1",
    )
    approval = EventRecord(
        event_id="child-approval",
        session_id=SESSION_ID,
        event_kind="approval_requested",
        payload={
            "run_id": "parent-run",
            "approval_request_id": "approval-1",
            "tool_call_id": "call-1",
            "tool_name": "write_file",
            "arguments": {"path": "src/file.py"},
            "effect_id": "child-effect",
            "attempt_id": "child-attempt",
            "target_run_id": "child-run",
            "target_parent_effect_id": "parent-effect",
            "subagent_child": True,
            "skip_parent_context": True,
        },
        created_at=datetime(2026, 8, 24, tzinfo=UTC),
        session_seq="2",
    )

    assert project_chat_event(internal, None) is None
    projected = project_chat_event(approval, parent)
    assert projected is not None
    assert projected.run_id == "parent-run"
    assert projected.payload == {
        "approval_request_id": "approval-1",
        "tool_call_id": "call-1",
        "tool_name": "write_file",
        "arguments": {"path": "src/file.py"},
        "effect_id": "child-effect",
        "attempt_id": "child-attempt",
        "target_run_id": "child-run",
        "target_parent_effect_id": "parent-effect",
    }


@pytest.mark.parametrize(
    ("event_kind", "payload", "expected_payload"),
    [
        (
            "assistant_message",
            {"run_id": "run-active", "content": "answer"},
            {"text": "answer"},
        ),
        (
            "tool_call",
            {
                "run_id": "run-active",
                "tool_call_id": "call-1",
                "tool_name": "file_read",
                "arguments": {"path": "README.md"},
            },
            {
                "call_id": "call-1",
                "tool_name": "file_read",
                "arguments": {"path": "README.md"},
            },
        ),
    ],
)
def test_project_chat_event_normalizes_agentkit_fact_payloads(
    event_kind: str,
    payload: dict[str, object],
    expected_payload: dict[str, object],
) -> None:
    record = EventRecord(
        event_id=f"event-{event_kind}",
        session_id=SESSION_ID,
        event_kind=event_kind,
        payload=payload,
        created_at=datetime(2026, 8, 24, tzinfo=UTC),
        session_seq="1",
    )

    projected = project_chat_event(record, _run("run-active"))

    assert projected is not None
    assert projected.payload == expected_payload


def test_project_chat_event_drops_empty_assistant_text() -> None:
    # Tool-call-only model rounds persist assistant_message facts with empty
    # content and no run_id. The projection must drop them instead of raising
    # on the empty text payload.
    record = EventRecord(
        event_id="event-empty-assistant",
        session_id=SESSION_ID,
        event_kind="assistant_message",
        payload={"content": ""},
        created_at=datetime(2026, 8, 24, tzinfo=UTC),
        session_seq="1",
    )

    assert project_chat_event(record, None) is None
    assert project_chat_event(record, _run("run-active")) is None


def test_project_chat_event_hides_superseded_run_facts() -> None:
    record = EventRecord(
        event_id="event-superseded-write",
        session_id=SESSION_ID,
        event_kind="assistant_message",
        payload={"content": "wrote file", "run_id": "run-old"},
        created_at=datetime(2026, 8, 24, tzinfo=UTC),
        session_seq="1",
    )

    assert project_chat_event(record, _run("run-old", superseded=True)) is None
    projected = project_chat_event(record, _run("run-old"))
    assert projected is not None
    assert projected.payload == {"text": "wrote file"}
    assert projected.run_id == "run-old"


@pytest.mark.asyncio
async def test_snapshot_chat_events_empty_and_nonempty_pages_are_bounded(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)

    empty = await store.snapshot_chat_events(SESSION_ID, None, 2)
    assert empty.events == ()
    empty_cursor = decode_chat_cursor(
        empty.snapshot_cursor,
        expected_session_id=SESSION_ID,
        fact_state=(await store.load_session_fact_source(SESSION_ID)),
    )
    assert empty_cursor.after_seq == "0"
    assert empty_cursor.high_water_seq == "0"
    assert empty.next_cursor is None

    await _append_chat(store, owner, "one", run_state=_run("run-active"))
    await _append_chat(store, owner, "two")
    await _append_chat(store, owner, "three")

    first = await store.snapshot_chat_events(SESSION_ID, None, 2)
    assert [event.source_event_id for event in first.events] == ["chat-one", "chat-two"]
    assert [event.session_seq for event in first.events] == ["1", "2"]
    assert [event.payload for event in first.events] == [
        {"text": "message-one"},
        {"text": "message-two"},
    ]
    assert first.next_cursor is not None

    await _append_chat(store, owner, "four")
    second_cursor = decode_chat_cursor(
        first.next_cursor,
        expected_session_id=SESSION_ID,
        fact_state=(await store.load_session_fact_source(SESSION_ID)),
    )
    second = await store.snapshot_chat_events(SESSION_ID, second_cursor, 2)
    assert [event.source_event_id for event in second.events] == ["chat-three"]
    assert second.next_cursor is None
    snapshot_cursor = decode_chat_cursor(
        second.snapshot_cursor,
        expected_session_id=SESSION_ID,
        fact_state=(await store.load_session_fact_source(SESSION_ID)),
    )
    assert snapshot_cursor.high_water_seq == "3"


@pytest.mark.asyncio
async def test_snapshot_excludes_superseded_runs_but_audit_retains_records(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    await _append_chat(
        store,
        owner,
        "superseded",
        run_id="run-superseded",
        run_state=_run("run-superseded", superseded=True),
    )
    await _append_chat(
        store,
        owner,
        "active",
        run_id="run-active",
        run_state=_run("run-active"),
    )
    await _append_chat(store, owner, "session", run_id=None)

    snapshot = await store.snapshot_chat_events(SESSION_ID, None, 10)

    assert [event.source_event_id for event in snapshot.events] == [
        "chat-active",
        "chat-session",
    ]
    assert (
        await store.load_event_record(SESSION_ID, "1")
    ).event_id == "chat-superseded"
    raw = await store.replay_raw(
        replace(snapshot_cursor_to_raw(snapshot), session_seq="0")
    )
    assert [event.event_id for event in raw] == [
        "chat-superseded",
        "chat-active",
        "chat-session",
    ]


def snapshot_cursor_to_raw(snapshot: Any) -> Any:
    from coding_agent.stores.runtime_store import RawCursor

    return RawCursor(session_id=snapshot.session_id, session_seq="0")


@pytest.mark.asyncio
async def test_snapshot_cursor_rejects_restore_epoch_and_retention_expiry(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    await _append_chat(store, owner, "one", run_state=_run("run-active"))
    await _append_chat(store, owner, "two")
    snapshot = await store.snapshot_chat_events(SESSION_ID, None, 1)
    assert snapshot.next_cursor is not None

    old_state = await store.load_session_fact_source(SESSION_ID)
    cursor = decode_chat_cursor(
        snapshot.next_cursor,
        expected_session_id=SESSION_ID,
        fact_state=old_state,
    )
    await _restore(store, owner)
    with pytest.raises(ChatCursorError) as wrong_epoch:
        await store.snapshot_chat_events(SESSION_ID, cursor, 1)
    assert (
        wrong_epoch.value.status,
        wrong_epoch.value.code,
        wrong_epoch.value.replay_required,
    ) == (
        409,
        "cursor_wrong_epoch",
        True,
    )

    await _append_chat(
        store,
        owner,
        "three",
        run_id="run-new",
        run_state=_run("run-new"),
    )
    await _append_chat(store, owner, "four", run_id="run-new")
    current = await store.snapshot_chat_events(SESSION_ID, None, 1)
    current_state = await store.load_session_fact_source(SESSION_ID)
    current_cursor = decode_chat_cursor(
        current.next_cursor,
        expected_session_id=SESSION_ID,
        fact_state=current_state,
    )
    await store.raise_retention_floor(owner, "5")
    with pytest.raises(ChatCursorError) as expired:
        await store.snapshot_chat_events(SESSION_ID, current_cursor, 1)
    assert (
        expired.value.status,
        expired.value.code,
        expired.value.replay_required,
    ) == (
        410,
        "cursor_expired",
        True,
    )


@pytest.mark.asyncio
async def test_fresh_snapshot_starts_at_retention_floor_with_exclusive_cursor(
    store_kind: str, tmp_path: Path
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    await _append_chat(store, owner, "one", run_state=_run("run-active"))
    await _append_chat(store, owner, "two")
    await _append_chat(store, owner, "three")
    await store.raise_retention_floor(owner, "2")

    snapshot = await store.snapshot_chat_events(SESSION_ID, None, 10)

    assert [event.session_seq for event in snapshot.events] == ["2", "3"]
    cursor = decode_chat_cursor(
        snapshot.snapshot_cursor,
        expected_session_id=SESSION_ID,
        fact_state=await store.load_session_fact_source(SESSION_ID),
    )
    assert cursor.after_seq == "1"
    assert cursor.high_water_seq == "3"


def test_projector_rejects_foreign_or_missing_run_metadata() -> None:
    record = EventRecord(
        event_id="foreign-event",
        session_id="session-a",
        event_kind="assistant_message",
        payload={"run_id": "foreign-run", "text": "must not leak"},
        created_at=datetime(2026, 8, 24, tzinfo=UTC),
        session_seq="1",
        projection_epoch="0",
    )
    foreign = replace(_run("foreign-run"), session_id="session-b")

    with pytest.raises(ChatProjectionCorruptionError):
        project_chat_event(record, foreign)
    with pytest.raises(ChatProjectionCorruptionError):
        project_chat_event(record, None)


@pytest.mark.asyncio
async def test_snapshot_rejects_foreign_or_missing_run_data(
    store_kind: str, tmp_path: Path
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    await _append_chat(store, owner, "missing", run_id="missing-run")

    with pytest.raises(ChatProjectionCorruptionError):
        await store.snapshot_chat_events(SESSION_ID, None, 10)


@pytest.mark.asyncio
async def test_snapshot_uses_bounded_joined_queries_for_large_hidden_prefix(
    store_kind: str, tmp_path: Path
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    for index in range(40):
        run_id = f"hidden-{index}"
        await _append_chat(
            store,
            owner,
            run_id,
            run_id=run_id,
            run_state=_run(run_id, superseded=True),
        )
    await _append_chat(
        store,
        owner,
        "visible",
        run_id="visible-run",
        run_state=_run("visible-run"),
    )
    if store_kind == "pg":
        store._harness_pool.connection.calls.clear()

    snapshot = await store.snapshot_chat_events(SESSION_ID, None, 1)

    assert [event.source_event_id for event in snapshot.events] == ["chat-visible"]
    if store_kind == "pg":
        calls = store._harness_pool.connection.calls
        event_queries = [
            query
            for kind, query in calls
            if kind == "fetch" and "session_event_records" in query
        ]
        run_queries = [
            query
            for kind, query in calls
            if kind == "fetchrow" and "FROM agent_runs" in query
        ]
        assert 1 < len(event_queries) <= 5
        assert run_queries == []
        assert all(
            "LEFT JOIN agent_runs" in query and "LIMIT" in query
            for query in event_queries
        )
