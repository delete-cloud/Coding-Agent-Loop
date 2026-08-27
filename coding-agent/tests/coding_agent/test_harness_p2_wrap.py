from __future__ import annotations

import asyncio
import inspect
import json
import types
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from starlette.requests import Request

from agentkit.checkpoint.models import CheckpointMeta, CheckpointSnapshot
from agentkit.runtime.context import AgentRunContext
from agentkit.tape.tape import Tape
from coding_agent.adapter.types import StopReason, TurnOutcome
from coding_agent.environment import LocalEnvironment
import coding_agent.server.http_server as http_server
from coding_agent.server.http.routes.prompts import send_prompt
from coding_agent.server.session_manager import SessionManager
from coding_agent.server.stores.session_owner_store import SQLiteSessionOwnerStore
from coding_agent.stores.local import local_sqlite_path, local_sqlite_storage_config
from coding_agent.stores.runtime_store import (
    AuthoritativeUnitOfWork,
    DEFAULT_HARNESS_PROJECTION,
    EffectLedgerSlot,
    EventRecord,
    MailboxDispositionSlot,
    ProjectionCursor,
)
from coding_agent.wire.protocol import (
    ApprovalRequest,
    ApprovalResponse,
    StreamDelta,
    ToolCallDelta,
)
from test_harness_p2_fact_source import (
    SESSION_ID,
    SESSION_PAYLOAD,
    _event,
    _open_store,
    _restore,
    _unit,
)

_TURN_SETTLED = "harness.TurnSettled"
_SESSION_MANAGER = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "coding_agent"
    / "server"
    / "session_manager.py"
)


def _durable_manager(tmp_path: Path) -> SessionManager:
    return SessionManager(
        storage_config=local_sqlite_storage_config(tmp_path),
        owner_store=SQLiteSessionOwnerStore(local_sqlite_path(tmp_path)),
        owner_id="owner-a",
        fencing_token=1,
    )


async def _run_successful_turn(manager: SessionManager, session_id: str) -> None:
    emitted_at = datetime(2026, 8, 20, 1, 0, 0)

    class FakeAdapter:
        def __init__(self, pipeline: object, ctx: object, consumer: Any) -> None:
            del pipeline
            self.ctx = ctx
            self.consumer = consumer

        async def run_turn(self, prompt: str) -> TurnOutcome:
            del prompt
            await self.consumer.emit(
                StreamDelta(
                    session_id=session_id,
                    agent_id="root",
                    timestamp=emitted_at,
                    content="hello",
                )
            )
            return TurnOutcome(stop_reason=StopReason.NO_TOOL_CALLS, steps_taken=1)

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs: object) -> tuple[object, object]:
        environment = kwargs["environment"]
        if not isinstance(environment, LocalEnvironment):
            raise TypeError("expected local environment")
        return fake_pipeline, types.SimpleNamespace(
            session_id=kwargs["session_id_override"],
            config={},
            tape=kwargs.get("tape") or Tape(),
            run_context=AgentRunContext(
                session_id=cast(str, kwargs["session_id_override"]),
                run_id=cast(str, kwargs["run_id_override"]),
                agent_id=None,
                environment=environment,
            ),
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.server.session_manager.PipelineAdapter", FakeAdapter)
        await manager.run_agent(session_id, "hello")


def _approval_request(
    session_id: str, request_id: str = "request-1"
) -> ApprovalRequest:
    return ApprovalRequest(
        session_id=session_id,
        request_id=request_id,
        tool_call=ToolCallDelta(
            session_id=session_id,
            tool_name="bash",
            arguments={"command": "pwd"},
            call_id=f"call-{request_id}",
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_duplicate_event_id_is_store_idempotent_and_keeps_mailbox(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    first_unit = _unit("one")
    first = await store.commit_authoritative_uow(owner, first_unit)
    replayed = AuthoritativeUnitOfWork(
        event=_event("one"),
        session_state={**SESSION_PAYLOAD, "turn": "replay"},
        mailbox=MailboxDispositionSlot(
            slot_id="mailbox-main",
            lane="user",
            disposition="settled",
            payload={"lane_cut": "replay"},
        ),
        effect=EffectLedgerSlot(
            effect_id="effect-1",
            status="prepared-one",
            payload={"attempt": "replay"},
        ),
    )

    second = await store.commit_authoritative_uow(owner, replayed)

    assert second.event.event_id == first.event.event_id
    assert second.event.session_seq == first.event.session_seq
    assert second.idempotent is True
    fact = await store.load_session_fact_source(SESSION_ID)
    assert fact is not None
    assert fact.session_seq == first.event.session_seq
    mailbox = await store.load_mailbox_slot(SESSION_ID, "mailbox-main")
    assert mailbox is not None
    assert mailbox.disposition == "settled"
    assert mailbox.payload == {"lane_cut": "replay"}
    assert await store.load_event_record(SESSION_ID, "2") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_restore_then_same_event_id_is_visible_in_current_epoch(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    first = await store.commit_authoritative_uow(owner, _unit("one"))
    assert first.event.event_id == "event-one"
    assert first.event.event_kind == "harness.TurnCommitted"
    assert first.event.payload == {"suffix": "one"}
    assert first.event.projection_epoch == "0"
    assert first.idempotent is False

    await _restore(store, owner)
    fact = await store.load_session_fact_source(SESSION_ID)
    assert fact is not None
    assert fact.projection_epoch == "1"
    assert fact.session_seq == first.event.session_seq

    replayed = AuthoritativeUnitOfWork(
        event=_event("one"),
        session_state={**SESSION_PAYLOAD, "turn": "replay-after-restore"},
        mailbox=MailboxDispositionSlot(
            slot_id="mailbox-main",
            lane="user",
            disposition="settled",
            payload={"lane_cut": "replay-after-restore"},
        ),
    )
    second = await store.commit_authoritative_uow(owner, replayed)

    assert second.idempotent is True
    assert second.event.event_id == first.event.event_id
    assert second.event.session_seq == first.event.session_seq
    assert second.event.event_kind == first.event.event_kind
    assert second.event.payload == first.event.payload
    assert second.event.projection_epoch == "1"
    assert second.projection_epoch == "1"
    after = await store.load_session_fact_source(SESSION_ID)
    assert after is not None
    assert after.session_seq == first.event.session_seq
    assert after.projection_epoch == "1"

    current = ProjectionCursor(
        kind="delta",
        session_id=SESSION_ID,
        projection=DEFAULT_HARNESS_PROJECTION,
        epoch="1",
        session_seq="0",
    )
    visible = await store.replay_projection(current)
    assert [event.event_id for event in visible] == ["event-one"]
    assert visible[0].event_kind == "harness.TurnCommitted"
    assert visible[0].payload == {"suffix": "one"}
    assert visible[0].projection_epoch == "1"

    third = await store.commit_authoritative_uow(
        owner,
        AuthoritativeUnitOfWork(
            event=_event("one"),
            session_state={**SESSION_PAYLOAD, "turn": "same-epoch-again"},
            mailbox=MailboxDispositionSlot(
                slot_id="mailbox-main",
                lane="user",
                disposition="settled",
                payload={"lane_cut": "same-epoch-again"},
            ),
        ),
    )
    assert third.idempotent is True
    assert third.event.session_seq == first.event.session_seq
    assert third.event.projection_epoch == "1"
    fact_again = await store.load_session_fact_source(SESSION_ID)
    assert fact_again is not None
    assert fact_again.session_seq == first.event.session_seq
    mailbox = await store.load_mailbox_slot(SESSION_ID, "mailbox-main")
    assert mailbox is not None
    assert mailbox.disposition == "settled"
    assert mailbox.payload == {"lane_cut": "same-epoch-again"}
    assert await store.load_event_record(SESSION_ID, "2") is None


@pytest.mark.asyncio
async def test_duplicate_boundary_uow_keeps_mailbox_settled(tmp_path: Path) -> None:
    manager = _durable_manager(tmp_path)
    session_id = await manager.create_session()
    store = manager._local_durable_store
    assert store is not None
    await _run_successful_turn(manager, session_id)
    session = await manager.get_session_async(session_id)
    assert session.current_turn_id is not None
    slot_id = f"turn:{session.current_turn_id}"
    with store._connect() as connection:
        connection.execute(
            """
            UPDATE session_mailbox_slots
            SET disposition = 'in_flight'
            WHERE session_id = ? AND slot_id = ?
            """,
            (session_id, slot_id),
        )
    clobbered = await store.load_mailbox_slot(session_id, slot_id)
    assert clobbered is not None
    assert clobbered.disposition == "in_flight"

    await manager._commit_session_uow(
        session,
        event_kind=_TURN_SETTLED,
        payload={
            "turn_id": session.current_turn_id,
            "turn_in_progress": session.turn_in_progress,
        },
        created_at=datetime.now(UTC),
        include_mailbox=True,
    )

    mailbox = await store.load_mailbox_slot(session_id, slot_id)
    assert mailbox is not None
    assert mailbox.disposition == "settled"
    replay = await store.replay_from_retention_floor(session_id)
    settled = [event for event in replay.events if event.event_kind == _TURN_SETTLED]
    assert len(settled) == 1


def test_commit_session_uow_does_not_parse_duplicate_event_id_text() -> None:
    source = _SESSION_MANAGER.read_text()
    assert "_is_duplicate_event_id_error" not in source
    assert 'if "event_id" in text' not in source
    commit_source = inspect.getsource(SessionManager._commit_session_uow)
    assert "IntegrityError" not in commit_source
    assert "UniqueViolationError" not in commit_source


@pytest.mark.asyncio
async def test_sse_disconnect_finalize_commits_turn_settled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disconnect_source = inspect.getsource(send_prompt)
    assert "persist_finalize=session_manager._persist_turn_settled" in disconnect_source
    assert "_sse_disconnect_turn_session_state" not in disconnect_source

    manager = _durable_manager(tmp_path)
    session_id = await manager.create_session()
    store = manager._local_durable_store
    assert store is not None
    turn_id = "run-disconnect"
    run_started = asyncio.Event()

    class FakeEventSourceResponse:
        def __init__(self, body_iterator, **kwargs: object) -> None:
            del kwargs
            self.body_iterator = body_iterator

    async def fake_run_agent(_session_id: str, _prompt: str, **_kwargs: object) -> None:
        nonlocal turn_id
        session = await manager.get_session_async(_session_id)
        if session.current_turn_id is not None:
            turn_id = session.current_turn_id
        else:
            session.current_turn_id = turn_id
        session.turn_in_progress = True
        session.turn_status = "running"
        await manager._persist_turn_started(session)
        run_started.set()
        await asyncio.Event().wait()

    async def fake_stream_wire_messages(
        wire: object,
        task: asyncio.Task[object] | None = None,
    ) -> AsyncIterator[dict[str, str]]:
        del wire, task
        await run_started.wait()
        yield {"event": "StreamDelta", "data": "{}"}
        await asyncio.Event().wait()

    monkeypatch.setattr(http_server, "EventSourceResponse", FakeEventSourceResponse)
    monkeypatch.setattr(http_server, "session_manager", manager)
    monkeypatch.setattr(http_server, "stream_wire_messages", fake_stream_wire_messages)
    monkeypatch.setattr(manager, "run_agent", fake_run_agent)

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": f"/sessions/{session_id}/prompt",
            "headers": [],
        }
    )
    response = await send_prompt(
        request,
        session_id,
        body=http_server.PromptRequest(prompt="Hello"),
        prompt=None,
        event_format="wire",
        api_key=None,
    )
    event_generator = cast(AsyncIterator[dict[str, str]], response.body_iterator)
    first_event = await asyncio.wait_for(anext(event_generator), timeout=1)
    assert first_event["event"] == "StreamDelta"

    before = await store.replay_from_retention_floor(session_id)
    assert [event.event_kind for event in before.events] == ["harness.TurnStarted"]
    inflight = await store.load_mailbox_slot(session_id, f"turn:{turn_id}")
    assert inflight is not None
    assert inflight.disposition == "in_flight"

    await asyncio.wait_for(event_generator.aclose(), timeout=1)

    replay = await store.replay_from_retention_floor(session_id)
    kinds = [event.event_kind for event in replay.events]
    assert kinds == ["harness.TurnStarted", _TURN_SETTLED]
    mailbox = await store.load_mailbox_slot(session_id, f"turn:{turn_id}")
    assert mailbox is not None
    assert mailbox.disposition == "settled"


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_restore_clears_stale_turn_mailbox(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    stale = AuthoritativeUnitOfWork(
        event=EventRecord(
            event_id="event-stale-turn",
            session_id=SESSION_ID,
            event_kind="harness.TurnStarted",
            payload={"turn_id": "run-after"},
            created_at=datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
        ),
        session_state=SESSION_PAYLOAD,
        mailbox=MailboxDispositionSlot(
            slot_id="turn:run-after",
            lane="turn",
            disposition="in_flight",
            payload={"run_id": "run-after"},
        ),
    )
    await store.commit_authoritative_uow(owner, stale)
    before = await store.load_mailbox_slot(SESSION_ID, "turn:run-after")
    assert before is not None
    assert before.disposition == "in_flight"

    await _restore(store, owner)

    after = await store.load_mailbox_slot(SESSION_ID, "turn:run-after")
    assert after is None


@pytest.mark.asyncio
async def test_effect_id_allocated_when_approval_wait_is_established(
    tmp_path: Path,
) -> None:
    manager = _durable_manager(tmp_path)
    session_id = await manager.create_session()
    store = manager._local_durable_store
    assert store is not None
    session = await manager.get_session_async(session_id)
    session.current_turn_id = "run-approval"
    session.turn_in_progress = True
    request = _approval_request(session_id)
    session.begin_approval_request(request)

    await manager._persist_approval_requested(session)

    effect_id = f"{session_id}:approval:{request.request_id}"
    effect = await store.load_effect_slot(session_id, effect_id)
    assert effect is not None
    assert effect.effect_id == effect_id
    assert effect.status == "prepared"
    assert effect.payload["request_id"] == request.request_id


@pytest.mark.asyncio
async def test_approval_decided_advances_same_effect_off_prepared(
    tmp_path: Path,
) -> None:
    manager = _durable_manager(tmp_path)
    session_id = await manager.create_session()
    store = manager._local_durable_store
    assert store is not None
    session = await manager.get_session_async(session_id)
    session.current_turn_id = "run-approval-decided"
    session.turn_in_progress = True
    request = _approval_request(session_id, request_id="request-decided")
    session.begin_approval_request(request)
    await manager._persist_approval_requested(session)
    effect_id = f"{session_id}:approval:{request.request_id}"
    prepared = await store.load_effect_slot(session_id, effect_id)
    assert prepared is not None
    assert prepared.status == "prepared"

    applied = session.approval_coordinator.respond(
        ApprovalResponse(
            session_id=session_id,
            request_id=request.request_id,
            approved=True,
            feedback="ok",
        )
    )
    assert applied is True
    session.expose_approval_response(
        {
            "request_id": request.request_id,
            "decision": "approve",
            "feedback": "ok",
        }
    )
    await manager._persist_approval_decided(session)

    decided = await store.load_effect_slot(session_id, effect_id)
    assert decided is not None
    assert decided.effect_id == effect_id
    assert decided.status == "settled"
    assert decided.payload["request_id"] == request.request_id


@pytest.mark.asyncio
async def test_restore_then_reapprove_reuses_same_effect_id(tmp_path: Path) -> None:
    manager = _durable_manager(tmp_path)
    session_id = await manager.create_session()
    store = manager._local_durable_store
    assert store is not None
    await _run_successful_turn(manager, session_id)
    session = await manager.get_session_async(session_id)
    request = _approval_request(session_id, request_id="request-restore")
    session.begin_approval_request(request)
    await manager._persist_approval_requested(session)
    effect_id = f"{session_id}:approval:{request.request_id}"
    first = await store.load_effect_slot(session_id, effect_id)
    assert first is not None
    assert first.effect_id == effect_id
    assert first.status == "prepared"
    assert first.payload["request_id"] == request.request_id

    authority = manager._owner_authorities[session_id]
    tape_id = session.tape_id
    assert tape_id is not None
    created_at = datetime(2026, 8, 20, 11, 0, tzinfo=UTC)
    await store.append_tape_entries(
        authority,
        tape_id,
        [{"kind": "message", "payload": {"text": "keep"}}],
    )
    snapshot = CheckpointSnapshot(
        meta=CheckpointMeta(
            checkpoint_id="checkpoint-keep",
            tape_id=tape_id,
            session_id=session_id,
            entry_count=1,
            window_start=0,
            created_at=created_at,
            label="keep",
        ),
        tape_entries=({"kind": "message", "payload": {"text": "keep"}},),
        plugin_states={},
    )
    await store.save_checkpoint(authority, snapshot)
    await store.restore_checkpoint_state(
        authority,
        snapshot,
        cast(dict[str, Any], session.to_store_data()),
    )
    with store._connect() as connection:
        connection.execute(
            """
            UPDATE session_effect_slots
            SET status = 'stale-after-restore', payload = ?
            WHERE session_id = ? AND effect_id = ?
            """,
            (
                json.dumps({"clobbered": True}, sort_keys=True),
                session_id,
                effect_id,
            ),
        )
    clobbered = await store.load_effect_slot(session_id, effect_id)
    assert clobbered is not None
    assert clobbered.status == "stale-after-restore"

    session.begin_approval_request(request)
    await manager._persist_approval_requested(session)
    reused = await store.load_effect_slot(session_id, effect_id)
    assert reused is not None
    assert reused.effect_id == effect_id
    assert reused.status == "prepared"
    assert reused.payload["request_id"] == request.request_id
