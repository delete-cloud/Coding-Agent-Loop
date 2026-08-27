from __future__ import annotations

import types
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from agentkit.runtime.context import AgentRunContext
from agentkit.tape.tape import Tape
from coding_agent.adapter.types import StopReason, TurnOutcome
from coding_agent.environment import LocalEnvironment
from coding_agent.runs import (
    ExternalWorkerExecutorRef,
    ExternalWorkerWorkspaceRef,
    IsolationPolicy,
    RemoteLoopOwnershipRetired,
    RunTarget,
)
from coding_agent.server.session_manager import SessionManager
from coding_agent.server.stores.session_owner_store import SQLiteSessionOwnerStore
from coding_agent.stores.local import local_sqlite_path, local_sqlite_storage_config
from coding_agent.wire.protocol import StreamDelta

from coding_agent.events.connected_chat import CHAT_EVENT_KINDS

_TURN_STARTED = "harness.TurnStarted"
_TURN_SETTLED = "harness.TurnSettled"
_SESSION_PERSISTED = "harness.SessionPersisted"
_TURN_MIDFLIGHT = "harness.TurnMidFlight"
_APPROVAL_KINDS = frozenset({"harness.ApprovalRequested", "harness.ApprovalDecided"})
_CHAT_KINDS = frozenset(CHAT_EVENT_KINDS)
_SOURCE_PATH = (
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


def _external_worker_run_target() -> RunTarget:
    return RunTarget(
        workspace=ExternalWorkerWorkspaceRef(),
        executor=ExternalWorkerExecutorRef(executor_kind="local_cli"),
        isolation=IsolationPolicy(kind="external_worker_policy"),
    )


async def _run_successful_turn(
    manager: SessionManager,
    session_id: str,
    *,
    delta_count: int = 3,
) -> None:
    emitted_at = datetime(2026, 8, 19, 21, 0, 0)

    class FakeAdapter:
        def __init__(self, pipeline: object, ctx: object, consumer: Any) -> None:
            del pipeline
            self.ctx = ctx
            self.consumer = consumer

        async def run_turn(self, prompt: str) -> TurnOutcome:
            del prompt
            for index in range(delta_count):
                await self.consumer.emit(
                    StreamDelta(
                        session_id=session_id,
                        agent_id="root",
                        timestamp=emitted_at,
                        content=f"hello-{index}",
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


@pytest.mark.asyncio
async def test_successful_turn_commits_uow_event_and_mailbox(tmp_path: Path) -> None:
    manager = _durable_manager(tmp_path)
    session_id = await manager.create_session()
    store = manager._local_durable_store
    assert store is not None

    await _run_successful_turn(manager, session_id)

    fact = await store.load_session_fact_source(session_id)
    assert fact is not None
    assert int(fact.session_seq) >= 1
    event = await store.load_event_record(session_id, "1")
    assert event is not None
    assert event.session_id == session_id

    session = await manager.get_session_async(session_id)
    assert session.current_turn_id is not None
    mailbox = await store.load_mailbox_slot(
        session_id, f"turn:{session.current_turn_id}"
    )
    assert mailbox is not None
    assert mailbox.lane == "turn"
    assert mailbox.payload["run_id"] == session.current_turn_id

    runs = await manager._require_runtime_store().list_agent_runs(session_id)
    assert runs
    assert runs[-1].tape_id is not None


@pytest.mark.asyncio
async def test_successful_turn_mailbox_disposition_is_settled(tmp_path: Path) -> None:
    manager = _durable_manager(tmp_path)
    session_id = await manager.create_session()
    store = manager._local_durable_store
    assert store is not None

    await _run_successful_turn(manager, session_id)

    session = await manager.get_session_async(session_id)
    assert session.turn_in_progress is False
    assert session.current_turn_id is not None
    mailbox = await store.load_mailbox_slot(
        session_id, f"turn:{session.current_turn_id}"
    )
    assert mailbox is not None
    assert mailbox.disposition == "settled"

    runs = await manager._require_runtime_store().list_agent_runs(session_id)
    assert runs
    assert runs[-1].status == "completed"


def _authoritative_kinds(events: list[Any]) -> list[str]:
    return [event.event_kind for event in events]


@pytest.mark.asyncio
async def test_successful_turn_authoritative_events_are_bounded_and_readable(
    tmp_path: Path,
) -> None:
    manager = _durable_manager(tmp_path)
    session_id = await manager.create_session()
    store = manager._local_durable_store
    assert store is not None

    await _run_successful_turn(manager, session_id, delta_count=24)

    replay = await store.replay_from_retention_floor(session_id)
    kinds = _authoritative_kinds(replay.events)
    assert _TURN_MIDFLIGHT not in kinds
    assert _SESSION_PERSISTED not in kinds
    assert _TURN_MIDFLIGHT not in _SOURCE_PATH.read_text()
    assert kinds.count(_TURN_STARTED) == 1
    assert kinds.count(_TURN_SETTLED) == 1
    approval_count = sum(1 for kind in kinds if kind in _APPROVAL_KINDS)
    chat_count = sum(1 for kind in kinds if kind in _CHAT_KINDS)
    assert 2 <= len(kinds) <= 2 + approval_count + chat_count
    assert len(kinds) != 24
    assert set(kinds) <= {_TURN_STARTED, _TURN_SETTLED, *_APPROVAL_KINDS, *_CHAT_KINDS}


@pytest.mark.asyncio
async def test_successful_turn_event_ids_are_deterministic(tmp_path: Path) -> None:
    manager = _durable_manager(tmp_path)
    session_id = await manager.create_session()
    store = manager._local_durable_store
    assert store is not None

    await _run_successful_turn(manager, session_id)

    session = await manager.get_session_async(session_id)
    assert session.current_turn_id is not None
    replay = await store.replay_from_retention_floor(session_id)
    assert replay.events
    for event in replay.events:
        if event.event_kind in _CHAT_KINDS:
            continue
        expected = f"{session_id}:{event.event_kind}:{session.current_turn_id}"
        if event.event_kind in _APPROVAL_KINDS:
            assert event.event_id.startswith(f"{expected}:")
            continue
        assert event.event_id == expected


@pytest.mark.asyncio
async def test_persist_session_does_not_append_or_advance_seq(
    tmp_path: Path,
) -> None:
    manager = _durable_manager(tmp_path)
    session_id = await manager.create_session()
    store = manager._local_durable_store
    assert store is not None

    await _run_successful_turn(manager, session_id)
    before = await store.load_session_fact_source(session_id)
    assert before is not None
    replay_before = await store.replay_from_retention_floor(session_id)
    event_ids = [event.event_id for event in replay_before.events]

    session = await manager.get_session_async(session_id)
    session.last_activity = datetime(2026, 8, 19, 22, 0, 0)
    await manager._persist_session_async(session)

    after = await store.load_session_fact_source(session_id)
    assert after is not None
    assert after.session_seq == before.session_seq
    replay_after = await store.replay_from_retention_floor(session_id)
    assert [event.event_id for event in replay_after.events] == event_ids
    assert _SESSION_PERSISTED not in _authoritative_kinds(replay_after.events)


@pytest.mark.asyncio
async def test_duplicate_boundary_uow_is_idempotent(tmp_path: Path) -> None:
    manager = _durable_manager(tmp_path)
    session_id = await manager.create_session()
    store = manager._local_durable_store
    assert store is not None

    await _run_successful_turn(manager, session_id)
    session = await manager.get_session_async(session_id)
    replay_before = await store.replay_from_retention_floor(session_id)
    before_ids = [event.event_id for event in replay_before.events]
    fact_before = await store.load_session_fact_source(session_id)
    assert fact_before is not None

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

    replay_after = await store.replay_from_retention_floor(session_id)
    assert [event.event_id for event in replay_after.events] == before_ids
    fact_after = await store.load_session_fact_source(session_id)
    assert fact_after is not None
    assert fact_after.session_seq == fact_before.session_seq


@pytest.mark.asyncio
async def test_successful_turn_persist_does_not_upsert_run_state(
    tmp_path: Path,
) -> None:
    manager = _durable_manager(tmp_path)
    session_id = await manager.create_session()
    store = manager._local_durable_store
    assert store is not None
    original = store.commit_authoritative_uow
    units: list[Any] = []

    async def spy(authority: object, unit: object) -> object:
        units.append(unit)
        return await original(authority, unit)

    store.commit_authoritative_uow = spy  # type: ignore[method-assign]
    await _run_successful_turn(manager, session_id)
    assert units
    assert all(
        getattr(unit, "run_state") is None
        or getattr(getattr(unit, "event", None), "event_kind", None) == "root_terminal"
        for unit in units
    )


@pytest.mark.asyncio
async def test_attached_session_run_agent_still_rejects_after_uow_wire(
    tmp_path: Path,
) -> None:
    manager = _durable_manager(tmp_path)
    session_id = await manager.create_session(
        default_run_target=_external_worker_run_target()
    )
    with pytest.raises(RemoteLoopOwnershipRetired, match="in-process"):
        await manager.run_agent(session_id, "hello")
    with pytest.raises(RemoteLoopOwnershipRetired, match="in-process"):
        await manager.request_attached_executor_run(session_id, "run on attached")
