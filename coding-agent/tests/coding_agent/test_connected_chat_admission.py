from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from coding_agent.events.connected_chat import (
    ChatCommandConflictError,
    ResumeSourceUnsettledError,
    TurnInProgressError,
)
from coding_agent.stores.runtime_store import AgentRunRecord
from tests.coding_agent.test_harness_p2_fact_source import (
    SESSION_ID,
    SESSION_PAYLOAD,
    TAPE_ID,
    _open_store,
)


@pytest.fixture(params=["sqlite", "pg"])
def store_kind(request: pytest.FixtureRequest) -> str:
    return str(request.param)


def _parent(run_id: str, status: str) -> AgentRunRecord:
    return AgentRunRecord(
        run_id=run_id,
        session_id=SESSION_ID,
        tape_id=TAPE_ID,
        parent_run_id=None,
        agent_id=None,
        status=status,
        started_at=datetime(2026, 8, 24, tzinfo=UTC),
        ended_at=(
            datetime(2026, 8, 24, 0, 1, tzinfo=UTC) if status == "completed" else None
        ),
        metadata={},
        result={},
    )


@pytest.mark.asyncio
async def test_prompt_admission_is_atomic_sqlite(tmp_path: Path) -> None:
    await _assert_atomic_admission("sqlite", tmp_path)


@pytest.mark.asyncio
async def test_prompt_admission_is_atomic_postgresql(tmp_path: Path) -> None:
    await _assert_atomic_admission("pg", tmp_path)


async def _assert_atomic_admission(kind: str, tmp_path: Path) -> None:
    store, owner = await _open_store(kind, tmp_path)
    prompt = "  exact prompt\nbytes  "

    admitted = await store.admit_chat_command(
        owner,
        prompt=prompt,
        command_id="command-1",
        parent_run_id=None,
        session_state=SESSION_PAYLOAD,
    )

    assert admitted.session_id == SESSION_ID
    assert admitted.command_id == "command-1"
    assert admitted.parent_run_id is None
    assert admitted.idempotent is False
    event = await store.load_event_record(SESSION_ID, "1")
    assert event is not None
    assert event.event_kind == "user_prompt"
    assert event.payload == {"run_id": admitted.run_id, "text": prompt}
    receipt = await store.load_receipt_slot(SESSION_ID, "chat-command:command-1")
    assert receipt is not None
    assert receipt.payload == {
        "command_id": "command-1",
        "parent_run_id": None,
        "prompt": prompt,
        "run_id": admitted.run_id,
    }
    run = await _load_run(store, kind, admitted.run_id)
    assert run is not None
    assert run.parent_run_id is None
    assert run.status == "requested"

    retried = await store.admit_chat_command(
        owner,
        prompt=prompt,
        command_id="command-1",
        parent_run_id=None,
        session_state=SESSION_PAYLOAD,
    )
    assert retried.run_id == admitted.run_id
    assert retried.session_seq == admitted.session_seq
    assert retried.idempotent is True
    fact = await store.load_session_fact_source(SESSION_ID)
    assert fact.session_seq == "1"

    with pytest.raises(ChatCommandConflictError):
        await store.admit_chat_command(
            owner,
            prompt=prompt + "different",
            command_id="command-1",
            parent_run_id=None,
            session_state=SESSION_PAYLOAD,
        )
    assert (await store.load_session_fact_source(SESSION_ID)).session_seq == "1"


@pytest.mark.asyncio
async def test_prompt_admission_rejection_writes_neither_run_nor_event(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    await store.commit_authoritative_uow(
        owner,
        admission_parent_unit(_parent("parent-running", "running")),
    )
    before = await _mutation_snapshot(store, store_kind)

    with pytest.raises(ResumeSourceUnsettledError):
        await store.admit_chat_command(
            owner,
            prompt="resume exactly",
            command_id="command-resume",
            parent_run_id="parent-running",
            session_state=SESSION_PAYLOAD,
        )

    assert await _mutation_snapshot(store, store_kind) == before
    assert (
        await store.load_receipt_slot(SESSION_ID, "chat-command:command-resume") is None
    )


def admission_parent_unit(parent: AgentRunRecord) -> Any:
    from coding_agent.stores.runtime_store import AuthoritativeUnitOfWork, EventRecord

    return AuthoritativeUnitOfWork(
        event=EventRecord(
            event_id="parent-seed",
            session_id=SESSION_ID,
            event_kind="harness.parent.seed",
            payload={},
            created_at=datetime(2026, 8, 24, tzinfo=UTC),
        ),
        session_state=SESSION_PAYLOAD,
        run_state=parent,
    )


async def _load_run(store: Any, kind: str, run_id: str) -> AgentRunRecord | None:
    if kind == "pg":
        row = store._harness_pool.connection.agent_runs.get(run_id)
        return None if row is None else AgentRunRecord(**row)
    from coding_agent.stores.runtime_store import SQLiteRuntimeStore

    return await SQLiteRuntimeStore(store._path).load_agent_run(run_id)


async def _save_run(store: Any, kind: str, run: AgentRunRecord) -> None:
    if kind == "pg":
        store._harness_pool.connection.agent_runs[run.run_id] = dict(run.__dict__)
        return
    from coding_agent.stores.runtime_store import SQLiteRuntimeStore

    await SQLiteRuntimeStore(store._path).update_agent_run(
        run.run_id,
        status=run.status,
        ended_at=run.ended_at,
        metadata=run.metadata,
        result=run.result,
        error=run.error,
    )


async def _mutation_snapshot(store: Any, kind: str) -> tuple[object, ...]:
    fact = await store.load_session_fact_source(SESSION_ID)
    if kind == "pg":
        connection = store._harness_pool.connection
        return (
            fact.session_seq,
            dict(connection.session_payloads.get(SESSION_ID, {})),
            tuple(sorted(connection.agent_runs)),
            tuple(
                (event["event_id"], event["session_seq"]) for event in connection.events
            ),
            tuple(sorted(connection.receipts)),
        )
    import sqlite3

    connection = sqlite3.connect(store._path)
    try:
        session = connection.execute(
            "SELECT payload FROM agent_http_sessions WHERE session_id = ?",
            (SESSION_ID,),
        ).fetchone()
        runs = tuple(
            row[0]
            for row in connection.execute(
                "SELECT run_id FROM agent_runs ORDER BY run_id"
            ).fetchall()
        )
        events = tuple(
            connection.execute(
                "SELECT event_id, session_seq FROM session_event_records ORDER BY session_seq"
            ).fetchall()
        )
        receipts = tuple(
            connection.execute(
                "SELECT receipt_id FROM session_receipt_slots ORDER BY receipt_id"
            ).fetchall()
        )
    finally:
        connection.close()
    return fact.session_seq, session, runs, events, receipts


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settled_status", ["completed", "failed", "cancelled", "interrupted"]
)
async def test_identical_retry_returns_original_without_rewriting_settled_state(
    store_kind: str, settled_status: str, tmp_path: Path
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    admitted = await store.admit_chat_command(
        owner,
        prompt="immutable prompt",
        command_id="immutable-command",
        parent_run_id=None,
        session_state=SESSION_PAYLOAD,
    )
    original = await _load_run(store, store_kind, admitted.run_id)
    assert original is not None
    settled = replace(
        original,
        status=settled_status,
        ended_at=datetime(2026, 8, 24, 1, 0, tzinfo=UTC),
        result={"settled": settled_status},
        error="settled failure" if settled_status == "failed" else None,
    )
    await _save_run(store, store_kind, settled)
    before = await _mutation_snapshot(store, store_kind)

    retried = await store.admit_chat_command(
        owner,
        prompt="immutable prompt",
        command_id="immutable-command",
        parent_run_id=None,
        session_state=SESSION_PAYLOAD,
    )

    assert retried == replace(admitted, idempotent=True)
    assert await _load_run(store, store_kind, admitted.run_id) == settled
    assert await _mutation_snapshot(store, store_kind) == before


@pytest.mark.asyncio
async def test_identical_retry_after_restore_does_not_promote_or_mutate(
    store_kind: str, tmp_path: Path
) -> None:
    from tests.coding_agent.test_harness_p2_fact_source import _restore

    store, owner = await _open_store(store_kind, tmp_path)
    admitted = await store.admit_chat_command(
        owner,
        prompt="restore prompt",
        command_id="restore-command",
        parent_run_id=None,
        session_state=SESSION_PAYLOAD,
    )
    run = await _load_run(store, store_kind, admitted.run_id)
    assert run is not None
    await _save_run(
        store,
        store_kind,
        replace(
            run,
            status="completed",
            ended_at=datetime(2026, 8, 24, 1, 0, tzinfo=UTC),
            result={"settled": True},
        ),
    )
    await _restore(store, owner)
    before = await _mutation_snapshot(store, store_kind)

    retried = await store.admit_chat_command(
        owner,
        prompt="restore prompt",
        command_id="restore-command",
        parent_run_id=None,
        session_state=SESSION_PAYLOAD,
    )
    assert retried == replace(admitted, idempotent=True)
    assert await _mutation_snapshot(store, store_kind) == before


@pytest.mark.asyncio
async def test_distinct_root_command_race_admits_exactly_one(
    store_kind: str, tmp_path: Path
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)

    results = await asyncio.gather(
        store.admit_chat_command(
            owner,
            prompt="first",
            command_id="race-first",
            parent_run_id=None,
            session_state=SESSION_PAYLOAD,
        ),
        store.admit_chat_command(
            owner,
            prompt="second",
            command_id="race-second",
            parent_run_id=None,
            session_state=SESSION_PAYLOAD,
        ),
        return_exceptions=True,
    )

    admissions = [result for result in results if not isinstance(result, Exception)]
    rejections = [
        result for result in results if isinstance(result, TurnInProgressError)
    ]
    assert len(admissions) == 1
    assert len(rejections) == 1
    snapshot = await _mutation_snapshot(store, store_kind)
    assert snapshot[0] == "1"
    assert len(snapshot[2]) == len(snapshot[3]) == len(snapshot[4]) == 1


@pytest.mark.asyncio
async def test_resume_requires_latest_active_settled_run_and_persists_lineage(
    store_kind: str, tmp_path: Path
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    older = replace(
        _parent("older", "completed"),
        started_at=datetime(2026, 8, 24, 0, 0, tzinfo=UTC),
        metadata={"last_event_id": "runtime-event-older"},
    )
    latest = replace(
        _parent("latest", "interrupted"),
        started_at=datetime(2026, 8, 24, 0, 2, tzinfo=UTC),
        ended_at=datetime(2026, 8, 24, 0, 3, tzinfo=UTC),
        metadata={"last_event_id": "runtime-event-latest"},
    )
    await store.commit_authoritative_uow(owner, admission_parent_unit(older))
    await store.commit_authoritative_uow(
        owner,
        replace(
            admission_parent_unit(latest),
            event=replace(
                admission_parent_unit(latest).event, event_id="parent-latest"
            ),
        ),
    )
    before = await _mutation_snapshot(store, store_kind)
    with pytest.raises(ResumeSourceUnsettledError):
        await store.admit_chat_command(
            owner,
            prompt="resume old",
            command_id="resume-old",
            parent_run_id="older",
            session_state=SESSION_PAYLOAD,
        )
    assert await _mutation_snapshot(store, store_kind) == before

    admitted = await store.admit_chat_command(
        owner,
        prompt="resume latest",
        command_id="resume-latest",
        parent_run_id="latest",
        session_state=SESSION_PAYLOAD,
    )
    resumed = await _load_run(store, store_kind, admitted.run_id)
    assert resumed is not None
    assert resumed.metadata == {
        "command_id": "resume-latest",
        "previous_run_id": "latest",
        "resume_from_run_id": "latest",
        "resume_from_event_id": "runtime-event-latest",
        "resume_reason": "user_resume",
        "resume_context_injected": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("source_case", ["missing", "foreign", "superseded"])
async def test_resume_rejects_invalid_source_without_any_admission_mutation(
    store_kind: str, source_case: str, tmp_path: Path
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    from coding_agent.stores.runtime_store import AuthoritativeUnitOfWork, EventRecord

    await store.commit_authoritative_uow(
        owner,
        AuthoritativeUnitOfWork(
            event=EventRecord(
                event_id="resume-invalid-baseline",
                session_id=SESSION_ID,
                event_kind="harness.resume.baseline",
                payload={},
                created_at=datetime(2026, 8, 24, tzinfo=UTC),
            ),
            session_state=SESSION_PAYLOAD,
        ),
    )
    parent_run_id = f"{source_case}-parent"
    if source_case != "missing":
        parent = replace(
            _parent(parent_run_id, "completed"),
            session_id="foreign-session" if source_case == "foreign" else SESSION_ID,
            tape_id=None if source_case == "foreign" else TAPE_ID,
            superseded_by_checkpoint_id=(
                "checkpoint-old" if source_case == "superseded" else None
            ),
            superseded_at=(
                datetime(2026, 8, 24, 0, 2, tzinfo=UTC)
                if source_case == "superseded"
                else None
            ),
        )
        if store_kind == "pg":
            store._harness_pool.connection.agent_runs[parent.run_id] = dict(
                parent.__dict__
            )
        else:
            from coding_agent.stores.runtime_store import SQLiteRuntimeStore

            await SQLiteRuntimeStore(store._path).create_agent_run(parent)
    before = await _mutation_snapshot(store, store_kind)

    with pytest.raises(ResumeSourceUnsettledError):
        await store.admit_chat_command(
            owner,
            prompt="invalid resume",
            command_id=f"resume-{source_case}",
            parent_run_id=parent_run_id,
            session_state=SESSION_PAYLOAD,
        )

    assert await _mutation_snapshot(store, store_kind) == before
