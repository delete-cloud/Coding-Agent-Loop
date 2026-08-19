from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from agentkit.checkpoint.models import CheckpointMeta, CheckpointSnapshot

from coding_agent.server.stores.session_owner_store import (
    OwnerAuthority,
    SessionOwnershipConflictError,
)
from coding_agent.stores.durable_local import SQLiteLocalDurableStore
from coding_agent.stores.durable_pg import PGDurableStore
from coding_agent.stores.runtime_store import (
    AgentRunRecord,
    AuthoritativeUnitOfWork,
    AuthoritativeWriteRefusedError,
    CursorEpochMismatchError,
    DEFAULT_HARNESS_PROJECTION,
    EffectLedgerSlot,
    EventRecord,
    JSONLRuntimeStore,
    KeyExpiredError,
    MailboxDispositionSlot,
    OperationReceiptSlot,
    ProjectionCursor,
    RawCursor,
    TrustedHandoff,
)


SESSION_ID = "session-a"
OWNER_ID = "owner-a"
TAPE_ID = "tape-a"
SESSION_PAYLOAD = {
    "id": SESSION_ID,
    "session_id": SESSION_ID,
    "tape_id": TAPE_ID,
    "status": "active",
}


def _event(suffix: str, *, created_at: datetime | None = None) -> EventRecord:
    stamp = created_at or datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    return EventRecord(
        event_id=f"event-{suffix}",
        session_id=SESSION_ID,
        event_kind="harness.TurnCommitted",
        payload={"suffix": suffix},
        created_at=stamp,
    )


def _unit(
    suffix: str,
    *,
    session_state: dict[str, object] | None = None,
    run_state: AgentRunRecord | None = None,
) -> AuthoritativeUnitOfWork:
    return AuthoritativeUnitOfWork(
        event=_event(suffix),
        session_state=session_state or {**SESSION_PAYLOAD, "turn": suffix},
        mailbox=MailboxDispositionSlot(
            slot_id="mailbox-main",
            lane="user",
            disposition=f"queued-{suffix}",
            payload={"lane_cut": suffix},
        ),
        effect=EffectLedgerSlot(
            effect_id="effect-1",
            status=f"prepared-{suffix}",
            payload={"attempt": suffix},
        ),
        receipt=OperationReceiptSlot(
            receipt_id="receipt-1",
            generation="1",
            payload={"op": suffix},
            compensation_effect_id="effect-1",
        ),
        run_state=run_state,
    )


def _run(run_id: str, *, started_at: datetime) -> AgentRunRecord:
    return AgentRunRecord(
        run_id=run_id,
        session_id=SESSION_ID,
        tape_id=TAPE_ID,
        parent_run_id=None,
        agent_id=None,
        status="running",
        started_at=started_at,
        metadata={"source": "harness-uow"},
        result={},
    )


class HarnessFakePGConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.owner_row: dict[str, object] | None = None
        self.session_payloads: dict[str, dict[str, object]] = {}
        self.session_tape_by_session: dict[str, str] = {}
        self.session_by_tape: dict[str, str] = {}
        self.fact_source: dict[str, dict[str, object]] = {}
        self.events: list[dict[str, object]] = []
        self.mailbox: dict[tuple[str, str], dict[str, object]] = {}
        self.effects: dict[tuple[str, str], dict[str, object]] = {}
        self.receipts: dict[tuple[str, str], dict[str, object]] = {}
        self.agent_runs: dict[str, dict[str, object]] = {}
        self.checkpoints: dict[str, dict[str, object]] = {}
        self.in_txn = False

    async def execute(self, query: str, *args: object) -> str:
        self.calls.append(("execute", query))
        if query.strip() == "BEGIN":
            self.in_txn = True
            return "BEGIN"
        if query.strip() == "COMMIT":
            self.in_txn = False
            return "COMMIT"
        if query.strip() == "ROLLBACK":
            self.in_txn = False
            return "ROLLBACK"
        if "INSERT INTO session_tapes" in query:
            session_id = cast(str, args[0])
            tape_id = cast(str, args[1])
            if (
                session_id not in self.session_tape_by_session
                and tape_id not in self.session_by_tape
            ):
                self.session_tape_by_session[session_id] = tape_id
                self.session_by_tape[tape_id] = session_id
            return "INSERT"
        if "INSERT INTO agent_http_sessions" in query:
            self.session_payloads[cast(str, args[0])] = cast(dict[str, object], args[1])
            return "INSERT"
        if "INSERT INTO session_fact_source" in query:
            session_id = cast(str, args[0])
            if session_id not in self.fact_source:
                self.fact_source[session_id] = {
                    "session_id": session_id,
                    "session_seq": args[1],
                    "retention_floor": args[2],
                    "projection": args[3],
                    "projection_epoch": args[4],
                    "trusted_handoff_seq": None,
                    "trusted_handoff_epoch": None,
                    "trusted_handoff_projection": None,
                    "trusted_handoff_payload": None,
                    "trusted_handoff_accepted_at": None,
                }
            return "INSERT"
        if "UPDATE session_fact_source" in query and "session_seq = $2" in query:
            row = self.fact_source[cast(str, args[0])]
            row["session_seq"] = args[1]
            return "UPDATE"
        if (
            "UPDATE session_fact_source" in query
            and "projection_epoch = projection_epoch + 1" in query
        ):
            row = self.fact_source.setdefault(
                cast(str, args[0]),
                {
                    "session_id": args[0],
                    "session_seq": 0,
                    "retention_floor": 0,
                    "projection": DEFAULT_HARNESS_PROJECTION,
                    "projection_epoch": 0,
                },
            )
            row["projection_epoch"] = int(row["projection_epoch"]) + 1
            return "UPDATE"
        if "UPDATE session_fact_source" in query and "retention_floor = $2" in query:
            self.fact_source[cast(str, args[0])]["retention_floor"] = args[1]
            return "UPDATE"
        if "UPDATE session_fact_source" in query and "trusted_handoff_seq" in query:
            row = self.fact_source[cast(str, args[0])]
            row["trusted_handoff_seq"] = args[1]
            row["trusted_handoff_epoch"] = args[2]
            row["trusted_handoff_projection"] = args[3]
            row["trusted_handoff_payload"] = args[4]
            row["trusted_handoff_accepted_at"] = args[5]
            return "UPDATE"
        if "INSERT INTO session_event_records" in query:
            self.events.append(
                {
                    "session_id": args[0],
                    "session_seq": args[1],
                    "event_id": args[2],
                    "event_kind": args[3],
                    "payload": args[4],
                    "created_at": args[5],
                    "projection_epoch": args[6],
                }
            )
            return "INSERT"
        if "INSERT INTO session_mailbox_slots" in query:
            self.mailbox[(cast(str, args[0]), cast(str, args[1]))] = {
                "session_id": args[0],
                "slot_id": args[1],
                "lane": args[2],
                "disposition": args[3],
                "payload": args[4],
            }
            return "INSERT"
        if "INSERT INTO session_effect_slots" in query:
            self.effects[(cast(str, args[0]), cast(str, args[1]))] = {
                "session_id": args[0],
                "effect_id": args[1],
                "status": args[2],
                "payload": args[3],
            }
            return "INSERT"
        if "INSERT INTO session_receipt_slots" in query:
            self.receipts[(cast(str, args[0]), cast(str, args[1]))] = {
                "session_id": args[0],
                "receipt_id": args[1],
                "generation": args[2],
                "payload": args[3],
                "compensation_effect_id": args[4],
            }
            return "INSERT"
        if "INSERT INTO agent_runs" in query:
            run_id = cast(str, args[0])
            self.agent_runs[run_id] = {
                "run_id": args[0],
                "session_id": args[1],
                "tape_id": args[2],
                "parent_run_id": args[3],
                "agent_id": args[4],
                "status": args[5],
                "started_at": args[6],
                "ended_at": args[7],
                "metadata": args[8],
                "result": args[9],
                "error": args[10],
                "superseded_by_checkpoint_id": args[11],
                "superseded_at": args[12],
            }
            return "INSERT"
        if "UPDATE agent_runs" in query and "superseded_at IS NULL" in query:
            return "UPDATE"
        if "DELETE FROM tape_entries" in query or "TRUNCATE" in query:
            return "DELETE"
        if "DELETE FROM checkpoints" in query:
            return "DELETE"
        if "INSERT INTO tape_entries" in query:
            return "INSERT"
        if "DELETE FROM topic_" in query or "UPDATE topics" in query:
            return "OK"
        return "OK"

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        self.calls.append(("fetchrow", query))
        if "FROM session_owners" in query:
            return self.owner_row
        if "FROM session_tapes" in query and "WHERE session_id" in query:
            tape_id = self.session_tape_by_session.get(cast(str, args[0]))
            return None if tape_id is None else {"tape_id": tape_id}
        if "FROM session_tapes" in query and "WHERE tape_id" in query:
            session_id = self.session_by_tape.get(cast(str, args[0]))
            return None if session_id is None else {"session_id": session_id}
        if "FROM session_fact_source" in query:
            return self.fact_source.get(cast(str, args[0]))
        if "FROM session_event_records" in query and "session_seq = $2" in query:
            session_id = cast(str, args[0])
            session_seq = args[1]
            for event in self.events:
                if (
                    event["session_id"] == session_id
                    and event["session_seq"] == session_seq
                ):
                    return event
            return None
        if "FROM session_mailbox_slots" in query:
            return self.mailbox.get((cast(str, args[0]), cast(str, args[1])))
        if "FROM session_effect_slots" in query:
            return self.effects.get((cast(str, args[0]), cast(str, args[1])))
        if "FROM session_receipt_slots" in query:
            return self.receipts.get((cast(str, args[0]), cast(str, args[1])))
        if "FROM agent_checkpoints" in query or "FROM checkpoints" in query:
            return self.checkpoints.get(
                cast(str, args[0]),
                {"meta": {"session_id": SESSION_ID, "tape_id": TAPE_ID}},
            )
        if "FROM agent_http_sessions" in query:
            payload = self.session_payloads.get(cast(str, args[0]))
            return None if payload is None else {"payload": payload}
        if "INSERT INTO session_fact_source" in query:
            await self.execute(query, *args)
            return self.fact_source.get(cast(str, args[0]))
        if "UPDATE session_fact_source" in query:
            await self.execute(query, *args)
            return self.fact_source.get(cast(str, args[0]))
        if "INSERT INTO session_event_records" in query:
            await self.execute(query, *args)
            return self.events[-1]
        if "INSERT INTO session_mailbox_slots" in query:
            await self.execute(query, *args)
            return self.mailbox[(cast(str, args[0]), cast(str, args[1]))]
        if "INSERT INTO session_effect_slots" in query:
            await self.execute(query, *args)
            return self.effects[(cast(str, args[0]), cast(str, args[1]))]
        if "INSERT INTO session_receipt_slots" in query:
            await self.execute(query, *args)
            return self.receipts[(cast(str, args[0]), cast(str, args[1]))]
        if "INSERT INTO agent_runs" in query:
            await self.execute(query, *args)
            return self.agent_runs[cast(str, args[0])]
        return None

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        self.calls.append(("fetch", query))
        if "FROM session_event_records" in query:
            session_id = cast(str, args[0])
            after = args[1]
            epoch_filter = None
            if "projection_epoch = $3" in query:
                epoch_filter = args[2]
                limit = int(args[3]) if len(args) > 3 else 1000
            else:
                limit = int(args[2]) if len(args) > 2 else 1000
            inclusive = "session_seq >= $2" in query
            rows = []
            for event in self.events:
                seq = event["session_seq"]
                if event["session_id"] != session_id:
                    continue
                if epoch_filter is not None and event["projection_epoch"] != epoch_filter:
                    continue
                if inclusive and seq >= after:
                    rows.append(event)
                if not inclusive and seq > after:
                    rows.append(event)
            return rows[:limit]
        return []


class HarnessFakePGPool:
    def __init__(self) -> None:
        self.connection = HarnessFakePGConnection()

    def seed_owner(self, authority: OwnerAuthority) -> None:
        self.connection.owner_row = {
            "owner_id": authority.owner_id,
            "lease_expires_at": datetime.now(UTC) + timedelta(seconds=30),
            "fencing_token": authority.epoch,
        }

    async def get_pool(self) -> HarnessFakePGPool:
        return self

    async def execute(self, query: str, *args: object) -> str:
        del query, args
        return "OK"

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        return await self.connection.fetchrow(query, *args)

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        return await self.connection.fetch(query, *args)

    async def acquire(self) -> HarnessFakePGConnection:
        return self.connection

    async def release(self, connection: HarnessFakePGConnection) -> None:
        del connection

    async def close(self) -> None:
        return None


async def _open_store(kind: str, tmp_path: Path) -> tuple[Any, OwnerAuthority]:
    if kind == "sqlite":
        store = SQLiteLocalDurableStore(tmp_path / "local.sqlite3")
        owner = await store.acquire_owner(SESSION_ID, OWNER_ID)
        await store.save_session(owner, SESSION_PAYLOAD)
        return store, owner
    if kind != "pg":
        raise ValueError(f"unknown store kind: {kind}")
    pool = HarnessFakePGPool()
    owner = OwnerAuthority(SESSION_ID, OWNER_ID, 1)
    pool.seed_owner(owner)
    store = PGDurableStore(pool=cast(Any, pool))
    store._harness_pool = pool  # type: ignore[attr-defined]
    await store.save_session(owner, SESSION_PAYLOAD)
    return store, owner


async def _restore(store: Any, owner: OwnerAuthority) -> None:
    created_at = datetime(2026, 8, 19, 11, 0, tzinfo=UTC)
    snapshot = CheckpointSnapshot(
        meta=CheckpointMeta(
            checkpoint_id="checkpoint-keep",
            tape_id=TAPE_ID,
            session_id=SESSION_ID,
            entry_count=1,
            window_start=0,
            created_at=created_at,
            label="keep",
        ),
        tape_entries=({"kind": "message", "payload": {"text": "keep"}},),
        plugin_states={},
    )
    if isinstance(store, SQLiteLocalDurableStore):
        await store.append_tape_entries(
            owner,
            TAPE_ID,
            [{"kind": "message", "payload": {"text": "keep"}}],
        )
        await store.save_checkpoint(owner, snapshot)
    else:
        pool = store._harness_pool
        pool.connection.session_tape_by_session[SESSION_ID] = TAPE_ID
        pool.connection.session_by_tape[TAPE_ID] = SESSION_ID
        pool.connection.checkpoints["checkpoint-keep"] = {
            "meta": {"session_id": SESSION_ID, "tape_id": TAPE_ID},
        }
    await store.restore_checkpoint_state(owner, snapshot, SESSION_PAYLOAD)


@pytest.fixture(params=["sqlite", "pg"])
def store_kind(request: pytest.FixtureRequest) -> str:
    return cast(str, request.param)


@pytest.mark.asyncio
async def test_authoritative_uow_commits_event_record_state_mailbox_effects_and_receipts(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    started_at = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    unit = _unit("one", run_state=_run("run-1", started_at=started_at))

    committed = await store.commit_authoritative_uow(owner, unit)

    assert committed.event.session_seq == "1"
    assert committed.event.projection_epoch == "0"
    assert committed.projection == DEFAULT_HARNESS_PROJECTION
    assert committed.raw_cursor.session_seq == "1"
    loaded = await store.load_event_record(SESSION_ID, "1")
    assert loaded is not None
    assert loaded.event_id == "event-one"
    assert loaded.payload == {"suffix": "one"}
    mailbox = await store.load_mailbox_slot(SESSION_ID, "mailbox-main")
    assert mailbox is not None
    assert mailbox.disposition == "queued-one"
    assert mailbox.payload == {"lane_cut": "one"}
    effect = await store.load_effect_slot(SESSION_ID, "effect-1")
    assert effect is not None
    assert effect.status == "prepared-one"
    receipt = await store.load_receipt_slot(SESSION_ID, "receipt-1")
    assert receipt is not None
    assert receipt.generation == "1"
    assert receipt.compensation_effect_id == "effect-1"
    fact = await store.load_session_fact_source(SESSION_ID)
    assert fact is not None
    assert fact.session_seq == "1"
    assert fact.projection_epoch == "0"
    if store_kind == "sqlite":
        assert store.load_session(SESSION_ID)["turn"] == "one"
        from coding_agent.stores.runtime_store import SQLiteRuntimeStore

        run = await SQLiteRuntimeStore(tmp_path / "local.sqlite3").load_agent_run(
            "run-1"
        )
        assert run is not None
        assert run.status == "running"
        assert run.metadata == {"source": "harness-uow"}
    else:
        pool = store._harness_pool
        begin_indexes = [
            index
            for index, (kind, query) in enumerate(pool.connection.calls)
            if kind == "execute" and query.strip() == "BEGIN"
        ]
        commit_indexes = [
            index
            for index, (kind, query) in enumerate(pool.connection.calls)
            if kind == "execute" and query.strip() == "COMMIT"
        ]
        assert begin_indexes
        assert commit_indexes
        uow_begin = None
        uow_commit = None
        for begin in begin_indexes:
            later_commits = [index for index in commit_indexes if index > begin]
            if not later_commits:
                continue
            commit = later_commits[0]
            queries = [query for _, query in pool.connection.calls[begin : commit + 1]]
            if any("session_event_records" in query for query in queries):
                uow_begin = begin
                uow_commit = commit
                break
        assert uow_begin is not None
        assert uow_commit is not None
        txn_queries = [
            query for _, query in pool.connection.calls[uow_begin : uow_commit + 1]
        ]
        assert any("session_owners" in query for query in txn_queries)
        assert any("session_event_records" in query for query in txn_queries)
        assert any("session_mailbox_slots" in query for query in txn_queries)
        assert any("session_effect_slots" in query for query in txn_queries)
        assert any("session_receipt_slots" in query for query in txn_queries)
        assert any("agent_http_sessions" in query for query in txn_queries)
        assert any("agent_runs" in query for query in txn_queries)
        assert pool.connection.in_txn is False
        assert "run-1" in pool.connection.agent_runs
        assert pool.connection.session_payloads[SESSION_ID]["turn"] == "one"

    stale = OwnerAuthority(SESSION_ID, "other-owner", 99)
    with pytest.raises(SessionOwnershipConflictError):
        await store.commit_authoritative_uow(stale, _unit("stale"))
    assert await store.load_event_record(SESSION_ID, "2") is None
    fact_after = await store.load_session_fact_source(SESSION_ID)
    assert fact_after is not None
    assert fact_after.session_seq == "1"


@pytest.mark.asyncio
async def test_jsonl_tape_is_derived_export_not_authoritative(tmp_path: Path) -> None:
    store = JSONLRuntimeStore(tmp_path / "runtime")
    owner = OwnerAuthority(SESSION_ID, OWNER_ID, 1)

    with pytest.raises(AuthoritativeWriteRefusedError, match="derived export"):
        await store.commit_authoritative_uow(owner, _unit("jsonl"))
    with pytest.raises(AuthoritativeWriteRefusedError, match="derived export"):
        await store.raise_retention_floor(owner, "1")
    with pytest.raises(AuthoritativeWriteRefusedError, match="derived export"):
        await store.accept_trusted_handoff(
            owner,
            TrustedHandoff(
                session_id=SESSION_ID,
                session_seq="1",
                projection=DEFAULT_HARNESS_PROJECTION,
                epoch="0",
            ),
        )


@pytest.mark.asyncio
async def test_session_seq_is_monotonic_per_session_across_restore_epochs(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    first = await store.commit_authoritative_uow(owner, _unit("one"))
    second = await store.commit_authoritative_uow(owner, _unit("two"))
    assert [first.event.session_seq, second.event.session_seq] == ["1", "2"]
    assert first.event.projection_epoch == "0"
    assert second.event.projection_epoch == "0"

    await _restore(store, owner)

    fact = await store.load_session_fact_source(SESSION_ID)
    assert fact is not None
    assert fact.session_seq == "2"
    assert fact.projection_epoch == "1"

    third = await store.commit_authoritative_uow(owner, _unit("three"))
    assert third.event.session_seq == "3"
    assert third.event.projection_epoch == "1"
    after = await store.load_session_fact_source(SESSION_ID)
    assert after is not None
    assert after.session_seq == "3"
    assert after.projection_epoch == "1"


@pytest.mark.asyncio
async def test_raw_cursor_follows_physical_log_across_epochs(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    await store.commit_authoritative_uow(owner, _unit("one"))
    second = await store.commit_authoritative_uow(owner, _unit("two"))
    pre_restore = RawCursor(
        session_id=SESSION_ID, session_seq=second.event.session_seq or "2"
    )

    await _restore(store, owner)
    third = await store.commit_authoritative_uow(owner, _unit("three"))

    replayed = await store.replay_raw(pre_restore)
    assert [event.event_id for event in replayed] == ["event-three"]
    assert replayed[0].session_seq == "3"
    assert replayed[0].projection_epoch == "1"
    from_start = await store.replay_raw(
        RawCursor(session_id=SESSION_ID, session_seq="0")
    )
    assert [event.event_id for event in from_start] == [
        "event-one",
        "event-two",
        "event-three",
    ]
    assert third.raw_cursor.session_seq == "3"


@pytest.mark.asyncio
async def test_delta_and_settled_cursors_bind_projection_and_epoch(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    first = await store.commit_authoritative_uow(owner, _unit("one"))
    epoch0 = first.event.projection_epoch
    assert epoch0 == "0"
    delta = ProjectionCursor(
        kind="delta",
        session_id=SESSION_ID,
        projection=DEFAULT_HARNESS_PROJECTION,
        epoch=epoch0,
        session_seq="0",
    )
    settled = ProjectionCursor(
        kind="settled",
        session_id=SESSION_ID,
        projection=DEFAULT_HARNESS_PROJECTION,
        epoch=epoch0,
        session_seq="0",
    )
    assert [event.event_id for event in await store.replay_projection(delta)] == [
        "event-one"
    ]
    assert [event.event_id for event in await store.replay_projection(settled)] == [
        "event-one"
    ]

    await _restore(store, owner)
    await store.commit_authoritative_uow(owner, _unit("two"))

    with pytest.raises(CursorEpochMismatchError, match="epoch"):
        await store.replay_projection(delta)
    with pytest.raises(CursorEpochMismatchError, match="epoch"):
        await store.replay_projection(settled)
    wrong_projection = ProjectionCursor(
        kind="delta",
        session_id=SESSION_ID,
        projection="other",
        epoch="1",
        session_seq="0",
    )
    with pytest.raises(CursorEpochMismatchError, match="projection"):
        await store.replay_projection(wrong_projection)

    rebound = ProjectionCursor(
        kind="delta",
        session_id=SESSION_ID,
        projection=DEFAULT_HARNESS_PROJECTION,
        epoch="1",
        session_seq="1",
    )
    replayed = await store.replay_projection(rebound)
    assert [event.event_id for event in replayed] == ["event-two"]
    assert replayed[0].projection_epoch == "1"


@pytest.mark.asyncio
async def test_replay_projection_does_not_return_superseded_epoch_events(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    await store.commit_authoritative_uow(owner, _unit("one"))
    await store.commit_authoritative_uow(owner, _unit("two"))
    await _restore(store, owner)
    await store.commit_authoritative_uow(owner, _unit("three"))

    rebuilt = ProjectionCursor(
        kind="delta",
        session_id=SESSION_ID,
        projection=DEFAULT_HARNESS_PROJECTION,
        epoch="1",
        session_seq="0",
    )
    replayed = await store.replay_projection(rebuilt)
    assert [event.event_id for event in replayed] == ["event-three"]
    assert [event.projection_epoch for event in replayed] == ["1"]


@pytest.mark.asyncio
async def test_authoritative_uow_rejects_unbound_or_foreign_run_tape(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    started_at = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    unbound = _unit(
        "unbound",
        run_state=replace(
            _run("run-unbound", started_at=started_at),
            tape_id="tape-of-other-session",
        ),
    )
    with pytest.raises(SessionOwnershipConflictError):
        await store.commit_authoritative_uow(owner, unbound)

    missing_tape = _unit(
        "missing",
        run_state=replace(
            _run("run-missing-tape", started_at=started_at),
            tape_id=None,
        ),
    )
    with pytest.raises(SessionOwnershipConflictError):
        await store.commit_authoritative_uow(owner, missing_tape)

    bound = _unit("bound", run_state=_run("run-bound", started_at=started_at))
    committed = await store.commit_authoritative_uow(owner, bound)
    assert committed.event.session_seq == "1"


@pytest.mark.asyncio
async def test_cross_host_key_expired_contract_lands_at_p2(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    await store.commit_authoritative_uow(owner, _unit("one"))
    await store.commit_authoritative_uow(owner, _unit("two"))
    await store.commit_authoritative_uow(owner, _unit("three"))
    await store.raise_retention_floor(owner, "3")

    stale = RawCursor(session_id=SESSION_ID, session_seq="0")
    with pytest.raises(KeyExpiredError) as expired:
        await store.replay_raw(stale)
    assert expired.value.retention_floor == "3"
    assert expired.value.cursor_seq == "0"

    floor_replay = await store.replay_from_retention_floor(SESSION_ID)
    assert [event.event_id for event in floor_replay.events] == ["event-three"]
    assert floor_replay.events[0].session_seq == "3"
    assert floor_replay.raw_cursor.session_id == SESSION_ID
    assert floor_replay.raw_cursor.session_seq == "3"

    with pytest.raises(CursorEpochMismatchError):
        await store.accept_trusted_handoff(
            owner,
            TrustedHandoff(
                session_id=SESSION_ID,
                session_seq="3",
                projection=DEFAULT_HARNESS_PROJECTION,
                epoch="9",
            ),
        )
    accepted = await store.accept_trusted_handoff(
        owner,
        TrustedHandoff(
            session_id=SESSION_ID,
            session_seq="3",
            projection=DEFAULT_HARNESS_PROJECTION,
            epoch="0",
            payload={"host": "replica-b"},
        ),
    )
    assert accepted.session_seq == "3"
    assert accepted.retention_floor == "3"
    assert accepted.projection_epoch == "0"
    assert accepted.trusted_handoff is not None
    assert accepted.trusted_handoff.payload == {"host": "replica-b"}
    assert accepted.trusted_handoff.session_seq == "3"
    reloaded = await store.load_session_fact_source(SESSION_ID)
    assert reloaded is not None
    assert reloaded.trusted_handoff is not None
    assert reloaded.trusted_handoff.payload == {"host": "replica-b"}
    assert reloaded.trusted_handoff.epoch == "0"
    after_floor = await store.raise_retention_floor(owner, "3")
    assert after_floor.trusted_handoff is not None
    assert after_floor.trusted_handoff.payload == {"host": "replica-b"}


@pytest.mark.asyncio
async def test_receipt_generation_and_effect_status_do_not_regress(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    first = _unit("one")
    advanced = AuthoritativeUnitOfWork(
        event=_event("high"),
        session_state={**SESSION_PAYLOAD, "turn": "high"},
        mailbox=first.mailbox,
        effect=EffectLedgerSlot(
            effect_id="effect-1",
            status="settled",
            payload={"attempt": "high"},
        ),
        receipt=OperationReceiptSlot(
            receipt_id="receipt-1",
            generation="5",
            payload={"op": "high"},
            compensation_effect_id="effect-1",
        ),
    )
    await store.commit_authoritative_uow(owner, first)
    await store.commit_authoritative_uow(owner, advanced)

    regress = AuthoritativeUnitOfWork(
        event=_event("low"),
        session_state={**SESSION_PAYLOAD, "turn": "low"},
        mailbox=first.mailbox,
        effect=EffectLedgerSlot(
            effect_id="effect-1",
            status="prepared",
            payload={"attempt": "low"},
        ),
        receipt=OperationReceiptSlot(
            receipt_id="receipt-1",
            generation="1",
            payload={"op": "low"},
            compensation_effect_id="effect-1",
        ),
    )
    await store.commit_authoritative_uow(owner, regress)

    effect = await store.load_effect_slot(SESSION_ID, "effect-1")
    assert effect is not None
    assert effect.status == "settled"
    assert effect.payload == {"attempt": "high"}
    receipt = await store.load_receipt_slot(SESSION_ID, "receipt-1")
    assert receipt is not None
    assert receipt.generation == "5"
    assert receipt.payload == {"op": "high"}


@pytest.mark.asyncio
async def test_receipt_generation_and_effect_status_can_advance(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    first = _unit("one")
    advanced = AuthoritativeUnitOfWork(
        event=_event("advance"),
        session_state={**SESSION_PAYLOAD, "turn": "advance"},
        mailbox=first.mailbox,
        effect=EffectLedgerSlot(
            effect_id="effect-1",
            status="dispatched",
            payload={"attempt": "advance"},
        ),
        receipt=OperationReceiptSlot(
            receipt_id="receipt-1",
            generation="2",
            payload={"op": "advance"},
            compensation_effect_id="effect-1",
        ),
    )
    await store.commit_authoritative_uow(owner, first)
    await store.commit_authoritative_uow(owner, advanced)

    effect = await store.load_effect_slot(SESSION_ID, "effect-1")
    assert effect is not None
    assert effect.status == "dispatched"
    assert effect.payload == {"attempt": "advance"}
    receipt = await store.load_receipt_slot(SESSION_ID, "receipt-1")
    assert receipt is not None
    assert receipt.generation == "2"
    assert receipt.payload == {"op": "advance"}


@pytest.mark.asyncio
async def test_uow_allows_optional_mailbox_effect_and_receipt(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    unit = AuthoritativeUnitOfWork(
        event=_event("bare"),
        session_state={**SESSION_PAYLOAD, "turn": "bare"},
    )

    committed = await store.commit_authoritative_uow(owner, unit)

    assert committed.event.session_seq == "1"
    assert await store.load_event_record(SESSION_ID, "1") is not None
    assert await store.load_mailbox_slot(SESSION_ID, "mailbox-main") is None
    assert await store.load_effect_slot(SESSION_ID, "effect-1") is None
    assert await store.load_receipt_slot(SESSION_ID, "receipt-1") is None


@pytest.mark.asyncio
async def test_empty_floor_replay_returns_resumable_cursor(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    first = await store.commit_authoritative_uow(owner, _unit("one"))
    await store.raise_retention_floor(owner, "2")

    replay = await store.replay_from_retention_floor(SESSION_ID)
    assert replay.events == []
    assert replay.raw_cursor.session_id == SESSION_ID
    assert replay.raw_cursor.session_seq == first.event.session_seq
    assert replay.complete is True

    second = await store.commit_authoritative_uow(owner, _unit("two"))
    continued = await store.replay_raw(replay.raw_cursor)
    assert [event.event_id for event in continued] == ["event-two"]
    assert continued[0].session_seq == second.event.session_seq


@pytest.mark.asyncio
async def test_truncated_floor_replay_cursor_lands_on_page_tail(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    for index in range(1, 11):
        await store.commit_authoritative_uow(owner, _unit(str(index)))

    page = await store.replay_from_retention_floor(SESSION_ID, limit=3)
    assert [event.event_id for event in page.events] == [
        "event-1",
        "event-2",
        "event-3",
    ]
    fact = await store.load_session_fact_source(SESSION_ID)
    assert fact is not None
    assert fact.session_seq == "10"
    assert page.raw_cursor.session_id == SESSION_ID
    assert page.raw_cursor.session_seq == "3"
    assert page.raw_cursor.session_seq != fact.session_seq
    assert page.complete is False

    continued = await store.replay_raw(page.raw_cursor)
    assert [event.event_id for event in continued] == [
        f"event-{index}" for index in range(4, 11)
    ]
    full = await store.replay_from_retention_floor(SESSION_ID, limit=20)
    assert full.complete is True
    assert full.raw_cursor.session_seq == "10"


def test_adr_0076_remains_proposed_and_0051_0053_remain_accepted() -> None:
    root = Path(__file__).resolve().parents[2] / "docs" / "adr"
    assert (
        "**Status**: Proposed" in (root / "0076-harness-control-plane.md").read_text()
    )
    for name in (
        "0051-external-worker-execution-control-plane.md",
        "0052-external-worker-usable-control-plane.md",
        "0053-advanced-external-worker-control-plane-foundations.md",
    ):
        text = (root / name).read_text()
        assert "**Status**: Accepted" in text
        assert "**Status**: Superseded" not in text
    adr_0076 = (root / "0076-harness-control-plane.md").read_text()
    assert "**Status**: Proposed" in adr_0076
    assert "legacy-only" in adr_0076
    assert "isolation contract (`test_cutover_session_rejects_bee_*`)" in adr_0076
    assert "remains deferred" in adr_0076


def test_bee_modules_are_marked_legacy_only() -> None:
    bee_root = Path(__file__).resolve().parents[2] / "src" / "coding_agent" / "bee"
    for path in sorted(bee_root.glob("*.py")):
        text = path.read_text()
        assert "legacy" in text.lower(), f"{path.name} must mark Bee as legacy"
