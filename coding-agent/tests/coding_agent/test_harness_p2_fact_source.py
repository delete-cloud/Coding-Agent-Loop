from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from agentkit.checkpoint.models import CheckpointMeta, CheckpointSnapshot
from agentkit.runtime.contracts import (
    AppliedCommandDisposition,
    EffectMutation,
    EffectPlan,
    EffectStatus,
    OperationStateCAS,
    ReconciliationOutcome,
    ReconciliationRecord,
    RejectedCommandDisposition,
    RuntimeCommand,
)

from coding_agent.server.stores.session_owner_store import (
    OwnerAuthority,
    SessionOwnershipConflictError,
)
from coding_agent.stores.durable_local import SQLiteLocalDurableStore
from coding_agent.stores.durable_pg import PGDurableStore
from coding_agent.stores.runtime_store import (
    AgentRunRecord,
    AuthoritativeUnitOfWork,
    CommandDispositionConflictError,
    RuntimeCommandAdmissionConflictError,
    AuthoritativeWriteRefusedError,
    CursorEpochMismatchError,
    DEFAULT_HARNESS_PROJECTION,
    EffectLedgerSlot,
    EffectMutationConflictError,
    EffectReconciliationEvidence,
    ExecutorAttemptConflictError,
    EventRecord,
    JSONLRuntimeStore,
    KeyExpiredError,
    MailboxDispositionSlot,
    OperationReceiptSlot,
    ProjectionCursor,
    RawCursor,
    TrustedHandoff,
    RecoveryEvidenceConflictError,
    StaleMailboxCutError,
    StateVersionConflictError,
    TransitionFingerprintMismatchError,
    state_value_with_reconciled_effect,
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
        self.reconciliation_evidence: dict[tuple[str, str], dict[str, object]] = {}
        self.executor_attempts: dict[tuple[str, str, str, str], dict[str, object]] = {}
        self.receipts: dict[tuple[str, str], dict[str, object]] = {}
        self.operation_states: dict[tuple[str, str], dict[str, object]] = {}
        self.transition_receipts: dict[tuple[str, int, str], dict[str, object]] = {}
        self.agent_runs: dict[str, dict[str, object]] = {}
        self.checkpoints: dict[str, dict[str, object]] = {}
        self.in_txn = False
        self.fail_on_operation_state_write = False
        self.fail_on_agent_run_write = False
        self._transaction_snapshot: dict[str, object] | None = None

    def _snapshot_transaction_state(self) -> dict[str, object]:
        return deepcopy(
            {
                "session_payloads": self.session_payloads,
                "session_tape_by_session": self.session_tape_by_session,
                "session_by_tape": self.session_by_tape,
                "fact_source": self.fact_source,
                "events": self.events,
                "mailbox": self.mailbox,
                "effects": self.effects,
                "reconciliation_evidence": self.reconciliation_evidence,
                "executor_attempts": self.executor_attempts,
                "receipts": self.receipts,
                "agent_runs": self.agent_runs,
                "checkpoints": self.checkpoints,
                "operation_states": self.operation_states,
                "transition_receipts": self.transition_receipts,
            }
        )

    def _restore_transaction_state(self) -> None:
        snapshot = self._transaction_snapshot
        if snapshot is None:
            return
        for field_name, value in snapshot.items():
            setattr(self, field_name, value)
        self._transaction_snapshot = None

    async def execute(self, query: str, *args: object) -> str:
        self.calls.append(("execute", query))
        if query.strip() == "BEGIN":
            self.in_txn = True
            self._transaction_snapshot = self._snapshot_transaction_state()
            return "BEGIN"
        if query.strip() == "COMMIT":
            self.in_txn = False
            self._transaction_snapshot = None
            return "COMMIT"
        if query.strip() == "ROLLBACK":
            self._restore_transaction_state()
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
                    "dispatch_generation": 0,
                    "projection": args[3],
                    "projection_epoch": args[4],
                    "trusted_handoff_seq": None,
                    "trusted_handoff_epoch": None,
                    "trusted_handoff_projection": None,
                    "trusted_handoff_payload": None,
                    "trusted_handoff_accepted_at": None,
                }
            return "INSERT"
        if (
            "UPDATE session_fact_source" in query
            and "dispatch_generation = $3" in query
        ):
            row = self.fact_source[cast(str, args[0])]
            row["session_seq"] = args[1]
            row["dispatch_generation"] = args[2]
            return "UPDATE"
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
                    "dispatch_generation": 0,
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
        if "UPDATE session_event_records" in query and "projection_epoch" in query:
            event_id = args[0]
            new_epoch = args[1]
            for event in self.events:
                if event["event_id"] == event_id:
                    event["projection_epoch"] = new_epoch
                    return "UPDATE"
            return "UPDATE"
        if (
            "INSERT INTO session_mailbox_slots" in query
            and "admitted_session_seq" in query
        ):
            self.mailbox[(cast(str, args[0]), cast(str, args[1]))] = {
                "session_id": args[0],
                "slot_id": args[1],
                "lane": "runtime",
                "disposition": "pending",
                "admitted_session_seq": args[2],
                "admitted_dispatch_generation": args[3],
                "payload": args[4],
            }
            return "INSERT"
        if "INSERT INTO session_mailbox_slots" in query:
            key = (cast(str, args[0]), cast(str, args[1]))
            row = self.mailbox.get(
                key,
                {
                    "session_id": args[0],
                    "slot_id": args[1],
                    "admitted_session_seq": None,
                    "admitted_dispatch_generation": None,
                },
            )
            row.update(
                {
                    "lane": args[2],
                    "disposition": args[3],
                    "payload": args[4],
                }
            )
            self.mailbox[key] = row
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
        if "INSERT INTO session_operation_states" in query:
            if self.fail_on_operation_state_write:
                self.fail_on_operation_state_write = False
                raise RuntimeError("injected transition crash")
            key = (cast(str, args[0]), cast(str, args[1]))
            self.operation_states[key] = {
                "session_id": args[0],
                "run_id": args[1],
                "revision": args[2],
                "projection_epoch": args[3],
                "transition_id": args[4],
                "fact_seq_start": args[5],
                "fact_seq_end": args[6],
                "value": args[7],
            }
            return "INSERT"
        if "INSERT INTO session_transition_receipts" in query:
            key = (cast(str, args[0]), cast(int, args[1]), cast(str, args[2]))
            if key not in self.transition_receipts:
                self.transition_receipts[key] = {
                    "session_id": args[0],
                    "projection_epoch": args[1],
                    "transition_id": args[2],
                    "mutation_fingerprint": args[3],
                    "result": args[4],
                }
            return "INSERT"
        if "INSERT INTO session_effect_reconciliation_evidence" in query:
            key = (cast(str, args[0]), cast(str, args[1]))
            self.reconciliation_evidence[key] = {
                "session_id": args[0],
                "evidence_ref": args[1],
                "effect_id": args[2],
                "attempt_id": args[3],
                "authorization_transition_id": args[4],
                "reconciliation_owner_epoch": args[5],
                "payload": args[6],
            }
            return "INSERT"
        if "INSERT INTO session_executor_attempts" in query:
            key = (
                cast(str, args[0]),
                cast(str, args[1]),
                cast(str, args[2]),
                cast(str, args[3]),
            )
            self.executor_attempts[key] = {
                "session_id": args[0],
                "effect_id": args[1],
                "attempt_id": args[2],
                "authorization_transition_id": args[3],
                "dispatch_owner_epoch": args[4],
                "status": args[5],
                "payload": args[6],
            }
            return "INSERT"
        if "UPDATE session_executor_attempts" in query:
            key = (
                cast(str, args[0]),
                cast(str, args[1]),
                cast(str, args[2]),
                cast(str, args[3]),
            )
            row = self.executor_attempts[key]
            row["status"] = args[4]
            row["payload"] = args[5]
            return "UPDATE"
        if "INSERT INTO agent_runs" in query:
            if self.fail_on_agent_run_write:
                self.fail_on_agent_run_write = False
                raise RuntimeError("injected terminal crash")
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
        if "DELETE FROM session_mailbox_slots" in query:
            session_id = cast(str, args[0])
            self.mailbox = {
                key: value
                for key, value in self.mailbox.items()
                if not (key[0] == session_id and str(key[1]).startswith("turn:"))
            }
            return "DELETE"
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
        if "FROM session_event_records" in query and "event_id = $1" in query:
            event_id = args[0]
            for event in self.events:
                if event["event_id"] == event_id:
                    return event
            return None
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
        if "FROM session_effect_reconciliation_evidence" in query:
            if "evidence_ref = $2" in query:
                return self.reconciliation_evidence.get(
                    (cast(str, args[0]), cast(str, args[1]))
                )
            for row in self.reconciliation_evidence.values():
                if (
                    row["session_id"] == args[0]
                    and row["effect_id"] == args[1]
                    and row["attempt_id"] == args[2]
                    and row["authorization_transition_id"] == args[3]
                ):
                    return row
            return None
        if "FROM session_executor_attempts" in query:
            return self.executor_attempts.get(
                (
                    cast(str, args[0]),
                    cast(str, args[1]),
                    cast(str, args[2]),
                    cast(str, args[3]),
                )
            )
        if "FROM session_receipt_slots" in query:
            return self.receipts.get((cast(str, args[0]), cast(str, args[1])))
        if "FROM session_operation_states" in query:
            return self.operation_states.get((cast(str, args[0]), cast(str, args[1])))
        if "FROM session_transition_receipts" in query:
            return self.transition_receipts.get(
                (cast(str, args[0]), cast(int, args[1]), cast(str, args[2]))
            )
        if (
            "FROM agent_runs" in query
            and "superseded_by_checkpoint_id IS NULL" in query
            and "ORDER BY started_at DESC" in query
        ):
            session_id = cast(str, args[0])
            active = [
                run
                for run in self.agent_runs.values()
                if run["session_id"] == session_id
                and run["superseded_by_checkpoint_id"] is None
            ]
            if not active:
                return None
            return max(active, key=lambda run: (run["started_at"], run["run_id"]))
        if "FROM agent_runs" in query and "run_id = $1" in query:
            run = self.agent_runs.get(cast(str, args[0]))
            if run is None or (len(args) > 1 and run["session_id"] != args[1]):
                return None
            return run
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
        if "UPDATE session_event_records" in query:
            await self.execute(query, *args)
            event_id = args[0]
            for event in self.events:
                if event["event_id"] == event_id:
                    return event
            return None
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
        if "INSERT INTO session_operation_states" in query:
            await self.execute(query, *args)
            return self.operation_states[(cast(str, args[0]), cast(str, args[1]))]
        if "INSERT INTO session_transition_receipts" in query:
            await self.execute(query, *args)
            return self.transition_receipts[
                (cast(str, args[0]), cast(int, args[1]), cast(str, args[2]))
            ]
        if "INSERT INTO session_effect_reconciliation_evidence" in query:
            await self.execute(query, *args)
            return self.reconciliation_evidence[
                (cast(str, args[0]), cast(str, args[1]))
            ]
        if "INSERT INTO session_executor_attempts" in query:
            await self.execute(query, *args)
            return self.executor_attempts[
                (
                    cast(str, args[0]),
                    cast(str, args[1]),
                    cast(str, args[2]),
                    cast(str, args[3]),
                )
            ]
        if "UPDATE session_executor_attempts" in query:
            await self.execute(query, *args)
            return self.executor_attempts[
                (
                    cast(str, args[0]),
                    cast(str, args[1]),
                    cast(str, args[2]),
                    cast(str, args[3]),
                )
            ]
        if "INSERT INTO agent_runs" in query:
            await self.execute(query, *args)
            return self.agent_runs[cast(str, args[0])]
        return None

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        self.calls.append(("fetch", query))
        if (
            "FROM session_fact_source AS source" in query
            and "LEFT JOIN session_mailbox_slots" in query
        ):
            session_id = cast(str, args[0])
            source = self.fact_source.get(session_id)
            if source is None:
                return []
            pending = [
                row
                for (row_session_id, _), row in self.mailbox.items()
                if row_session_id == session_id
                and row.get("admitted_session_seq") is not None
                and row.get("disposition") in {"pending", "admitted"}
            ]
            if not pending:
                return [
                    {
                        "dispatch_generation": source["dispatch_generation"],
                        "slot_id": None,
                        "disposition": None,
                        "admitted_session_seq": None,
                        "admitted_dispatch_generation": None,
                        "payload": None,
                    }
                ]
            return [
                {
                    **row,
                    "dispatch_generation": source["dispatch_generation"],
                }
                for row in sorted(
                    pending,
                    key=lambda item: cast(int, item["admitted_session_seq"]),
                )
            ]
        if "FROM session_event_records" in query:
            session_id = cast(str, args[0])
            after = args[1]
            epoch_filter = None
            high_water = None
            if "projection_epoch = $3" in query:
                epoch_filter = args[2]
                limit = int(args[3]) if len(args) > 3 else 1000
            elif "session_seq <= $3" in query:
                high_water = args[2]
                if "event_kind = ANY($4" in query:
                    limit = int(args[4]) if len(args) > 4 else 1000
                else:
                    limit = int(args[3]) if len(args) > 3 else 1000
            else:
                limit = int(args[2]) if len(args) > 2 else 1000
            inclusive = "session_seq >= $2" in query
            rows = []
            for event in self.events:
                seq = event["session_seq"]
                if event["session_id"] != session_id:
                    continue
                if (
                    epoch_filter is not None
                    and event["projection_epoch"] != epoch_filter
                ):
                    continue
                if high_water is not None and seq > high_water:
                    continue
                selected = inclusive and seq >= after or not inclusive and seq > after
                if selected:
                    joined = dict(event)
                    if "LEFT JOIN agent_runs" in query:
                        run_id = cast(dict[str, object], event["payload"]).get("run_id")
                        run = self.agent_runs.get(cast(str, run_id))
                        if run is not None and run["session_id"] == session_id:
                            joined.update(run)
                        else:
                            joined["run_id"] = None
                    rows.append(joined)
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


def _phase_b_pg_unit(
    transition_id: str,
    *,
    revision: int,
    state_value: dict[str, object] | None = None,
) -> AuthoritativeUnitOfWork:
    return AuthoritativeUnitOfWork(
        event=None,
        session_state={**SESSION_PAYLOAD, "transition": transition_id},
        transition_id=transition_id,
        state_cas=OperationStateCAS(
            run_id="run-phase-b-pg",
            revision=revision,
            projection_epoch=0,
        ),
        state_value=(
            {"transition": transition_id} if state_value is None else state_value
        ),
        facts=(
            EventRecord(
                event_id=f"fact-{transition_id}",
                session_id=SESSION_ID,
                event_kind="finalized_thinking",
                payload={"text": transition_id},
                created_at=datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
            ),
        ),
        dispositions=(
            AppliedCommandDisposition(command_id=f"applied-{transition_id}"),
            RejectedCommandDisposition(
                command_id=f"rejected-{transition_id}",
                reason_code="not_applicable",
            ),
        ),
        effect_mutation=EffectMutation.prepare(
            EffectPlan(
                effect_id=f"effect-{transition_id}",
                attempt_id=f"attempt-{transition_id}",
                effect_kind="tool",
                payload={"name": "read"},
            )
        ),
    )


def _phase_b_pg_dispatch_unit(
    transition_id: str,
    *,
    revision: int,
    effect_id: str,
    attempt_id: str,
    expected_mailbox_cut: str,
    state_value: dict[str, object] | None = None,
) -> AuthoritativeUnitOfWork:
    return AuthoritativeUnitOfWork(
        event=None,
        session_state={**SESSION_PAYLOAD, "transition": transition_id},
        transition_id=transition_id,
        state_cas=OperationStateCAS(
            run_id="run-phase-b-pg",
            revision=revision,
            projection_epoch=0,
        ),
        state_value=(
            {"transition": transition_id} if state_value is None else state_value
        ),
        effect_mutation=EffectMutation(
            effect_id=effect_id,
            attempt_id=attempt_id,
            expected_status=EffectStatus.PREPARED,
            status=EffectStatus.DISPATCHED,
            payload={"authorization_transition_id": transition_id},
        ),
        expected_mailbox_cut=expected_mailbox_cut,
    )


def _phase_d4_pg_state(
    *,
    effect_id: str,
    attempt_id: str,
    authorization_transition_id: str,
    unknown_input_id: str | None = None,
) -> dict[str, object]:
    active = {
        "effect_id": effect_id,
        "attempt_id": attempt_id,
        "tool_call_id": "call-pg-recovery",
        "tool_name": "read",
        "authorization_transition_id": authorization_transition_id,
        "dispatch_owner_epoch": 1,
    }
    runtime: dict[str, object] = {
        "pending_effect_plans": (
            {
                "effect_id": effect_id,
                "attempt_id": attempt_id,
                "effect_kind": "tool",
                "payload": {
                    "tool_call_id": "call-pg-recovery",
                    "tool_name": "read",
                },
                "requires_approval": False,
                "approval_request_id": None,
                "idempotency_key": None,
            },
        ),
        "active_effect_authorization": active,
        "mailbox_cut": 0,
    }
    if unknown_input_id is not None:
        runtime["unknown_effect"] = {
            **active,
            "indeterminate_input_id": unknown_input_id,
        }
    return {"_agentkit_runtime": runtime}


def _seed_phase_b_pg_commands(
    store: PGDurableStore,
    transition_id: str,
    *,
    disposition: str = "pending",
) -> None:
    pool = cast(HarnessFakePGPool, store._harness_pool)
    for prefix in ("applied", "rejected"):
        command_id = f"{prefix}-{transition_id}"
        pool.connection.mailbox[(SESSION_ID, command_id)] = {
            "session_id": SESSION_ID,
            "slot_id": command_id,
            "lane": "runtime",
            "disposition": disposition,
            "payload": {},
        }


@pytest.mark.asyncio
async def test_uow_commits_state_facts_dispositions_and_effect_ledger_atomically_postgresql(
    tmp_path: Path,
) -> None:
    store, owner = await _open_store("pg", tmp_path)
    _seed_phase_b_pg_commands(store, "pg-atomic", disposition="admitted")

    committed = await store.commit_authoritative_uow(
        owner,
        _phase_b_pg_unit("pg-atomic", revision=0),
    )

    assert committed.state_version is not None
    assert (
        await store.load_operation_state(SESSION_ID, "run-phase-b-pg")
        == committed.state_version
    )
    assert (
        await store.load_transition_receipt(SESSION_ID, 0, "pg-atomic")
        == committed.transition_receipt
    )
    assert committed.state_version.revision == 1
    assert committed.state_version.commit_ref.transition_id == "pg-atomic"
    assert committed.state_version.commit_ref.fact_seq_start == "1"
    assert committed.state_version.commit_ref.fact_seq_end == "1"
    rejected = await store.load_mailbox_slot(SESSION_ID, "rejected-pg-atomic")
    assert rejected is not None
    assert rejected.payload == {"reason_code": "not_applicable"}
    effect = await store.load_effect_slot(SESSION_ID, "effect-pg-atomic")
    assert effect is not None
    assert effect.status == "prepared"


@pytest.mark.asyncio
async def test_transition_receipt_same_epoch_retry_returns_stored_commit_before_cas_and_before_any_write_postgresql(
    tmp_path: Path,
) -> None:
    store, owner = await _open_store("pg", tmp_path)
    _seed_phase_b_pg_commands(store, "pg-retry")
    _seed_phase_b_pg_commands(store, "pg-later")
    original = _phase_b_pg_unit("pg-retry", revision=0)
    first = await store.commit_authoritative_uow(owner, original)
    later = await store.commit_authoritative_uow(
        owner,
        _phase_b_pg_unit("pg-later", revision=1),
    )

    replay = await store.commit_authoritative_uow(owner, original)

    assert replay.idempotent is True
    assert replay.state_version == first.state_version
    assert replay.facts == first.facts
    assert later.state_version is not None
    assert later.state_version.revision == 2
    assert await store.load_event_record(SESSION_ID, "3") is None


@pytest.mark.asyncio
async def test_stale_dispatch_authorization_writes_nothing_postgresql(
    tmp_path: Path,
) -> None:
    store, owner = await _open_store("pg", tmp_path)
    prepare_id = "pg-dispatch-prepare"
    effect_id = f"effect-{prepare_id}"
    _seed_phase_b_pg_commands(store, prepare_id)
    await store.commit_authoritative_uow(
        owner,
        _phase_b_pg_unit(prepare_id, revision=0),
    )
    first_cancel = await store.admit_runtime_command(
        owner,
        RuntimeCommand(
            command_id="pg-cancel-before-dispatch",
            command_kind="cancel",
            payload={},
        ),
    )
    assert first_cancel.mailbox_cut == "1"
    stale = _phase_b_pg_dispatch_unit(
        "pg-dispatch-authorize",
        revision=1,
        effect_id=effect_id,
        attempt_id=f"attempt-{prepare_id}",
        expected_mailbox_cut="0",
    )

    with pytest.raises(StaleMailboxCutError) as stale_error:
        await store.commit_authoritative_uow(owner, stale)

    assert stale_error.value.expected_mailbox_cut == 0
    assert stale_error.value.current_mailbox_cut == 1
    state_after_stale = await store.load_operation_state(
        SESSION_ID,
        "run-phase-b-pg",
    )
    assert state_after_stale is not None
    assert state_after_stale.revision == 1
    effect_after_stale = await store.load_effect_slot(SESSION_ID, effect_id)
    assert effect_after_stale is not None
    assert effect_after_stale.status == "prepared"
    assert (
        await store.load_transition_receipt(
            SESSION_ID,
            0,
            "pg-dispatch-authorize",
        )
        is None
    )

    authorized = _phase_b_pg_dispatch_unit(
        "pg-dispatch-authorize",
        attempt_id=f"attempt-{prepare_id}",
        revision=1,
        effect_id=effect_id,
        expected_mailbox_cut="1",
    )
    first = await store.commit_authoritative_uow(owner, authorized)
    executor_attempt = await store.load_executor_attempt(
        SESSION_ID,
        effect_id,
        f"attempt-{prepare_id}",
        "pg-dispatch-authorize",
    )
    assert executor_attempt is not None
    assert executor_attempt.status == "authorized_unclaimed"
    second_cancel = await store.admit_runtime_command(
        owner,
        RuntimeCommand(
            command_id="pg-cancel-after-dispatch",
            command_kind="cancel",
            payload={},
        ),
    )
    lease = datetime(2026, 8, 30, 12, 5, tzinfo=UTC)
    reserved = await store.reserve_executor_attempt(
        owner,
        effect_id=effect_id,
        attempt_id=f"attempt-{prepare_id}",
        authorization_transition_id="pg-dispatch-authorize",
        executor_id="pg-executor",
        lease_expires_at=lease,
    )
    assert (
        await store.reserve_executor_attempt(
            owner,
            effect_id=effect_id,
            attempt_id=f"attempt-{prepare_id}",
            authorization_transition_id="pg-dispatch-authorize",
            executor_id="pg-executor",
            lease_expires_at=lease,
        )
        == reserved
    )
    started = await store.mark_executor_attempt_started(
        owner,
        effect_id=effect_id,
        attempt_id=f"attempt-{prepare_id}",
        authorization_transition_id="pg-dispatch-authorize",
        executor_id="pg-executor",
        claim_generation=reserved.claim_generation,
        now=datetime(2026, 8, 30, 12, 1, tzinfo=UTC),
    )
    assert (
        await store.mark_executor_attempt_started(
            owner,
            effect_id=effect_id,
            attempt_id=f"attempt-{prepare_id}",
            authorization_transition_id="pg-dispatch-authorize",
            executor_id="pg-executor",
            claim_generation=reserved.claim_generation,
            now=datetime(2026, 8, 30, 12, 1, tzinfo=UTC),
        )
        == started
    )
    quiescent = await store.mark_executor_attempt_quiescent(
        owner,
        effect_id=effect_id,
        attempt_id=f"attempt-{prepare_id}",
        authorization_transition_id="pg-dispatch-authorize",
        executor_id="pg-executor",
        claim_generation=reserved.claim_generation,
        now=datetime(2026, 8, 30, 12, 2, tzinfo=UTC),
        evidence_ref="pg-quiescence",
    )
    assert (
        await store.mark_executor_attempt_quiescent(
            owner,
            effect_id=effect_id,
            attempt_id=f"attempt-{prepare_id}",
            authorization_transition_id="pg-dispatch-authorize",
            executor_id="pg-executor",
            claim_generation=reserved.claim_generation,
            now=datetime(2026, 8, 30, 12, 2, tzinfo=UTC),
            evidence_ref="pg-quiescence",
        )
        == quiescent
    )
    assert second_cancel.mailbox_cut == "2"

    replay = await store.commit_authoritative_uow(owner, authorized)

    assert replay.idempotent is True
    assert replay.state_version == first.state_version
    effect_after_replay = await store.load_effect_slot(SESSION_ID, effect_id)
    assert effect_after_replay is not None
    assert effect_after_replay.status == "dispatched"


@pytest.mark.asyncio
async def test_dispatch_authorization_exact_replay_precedes_newer_cut_postgresql(
    tmp_path: Path,
) -> None:
    store, owner = await _open_store("pg", tmp_path)
    prepare_id = "pg-dispatch-replay-prepare"
    effect_id = f"effect-{prepare_id}"
    _seed_phase_b_pg_commands(store, prepare_id)
    await store.commit_authoritative_uow(
        owner,
        _phase_b_pg_unit(prepare_id, revision=0),
    )
    authorized = _phase_b_pg_dispatch_unit(
        "pg-dispatch-replay-authorize",
        revision=1,
        effect_id=effect_id,
        attempt_id=f"attempt-{prepare_id}",
        expected_mailbox_cut="0",
    )
    first = await store.commit_authoritative_uow(owner, authorized)
    await store.admit_runtime_command(
        owner,
        RuntimeCommand(
            command_id="pg-cancel-after-replay-authorization",
            command_kind="cancel",
            payload={},
        ),
    )

    replay = await store.commit_authoritative_uow(owner, authorized)

    assert replay.idempotent is True
    assert replay.state_version == first.state_version


@pytest.mark.asyncio
async def test_postgresql_phase_b_cas_and_fingerprint_conflicts_write_nothing(
    tmp_path: Path,
) -> None:
    store, owner = await _open_store("pg", tmp_path)
    _seed_phase_b_pg_commands(store, "pg-original")
    _seed_phase_b_pg_commands(store, "pg-stale")
    original = _phase_b_pg_unit("pg-original", revision=0)
    await store.commit_authoritative_uow(owner, original)

    with pytest.raises(StateVersionConflictError):
        await store.commit_authoritative_uow(
            owner,
            _phase_b_pg_unit("pg-stale", revision=0),
        )
    with pytest.raises(TransitionFingerprintMismatchError):
        await store.commit_authoritative_uow(
            owner,
            _phase_b_pg_unit(
                "pg-original",
                revision=0,
                state_value={"different": True},
            ),
        )

    assert await store.load_event_record(SESSION_ID, "2") is None
    state = await store.load_operation_state(SESSION_ID, "run-phase-b-pg")
    assert state is not None
    assert state.revision == 1


@pytest.mark.asyncio
async def test_postgresql_transition_failure_rolls_back_every_mutation(
    tmp_path: Path,
) -> None:
    store, owner = await _open_store("pg", tmp_path)
    _seed_phase_b_pg_commands(store, "pg-crash")
    pool = cast(HarnessFakePGPool, store._harness_pool)
    pool.connection.fail_on_operation_state_write = True

    with pytest.raises(RuntimeError, match="injected transition crash"):
        await store.commit_authoritative_uow(
            owner,
            _phase_b_pg_unit("pg-crash", revision=0),
        )

    assert await store.load_event_record(SESSION_ID, "1") is None
    assert await store.load_operation_state(SESSION_ID, "run-phase-b-pg") is None
    mailbox = await store.load_mailbox_slot(SESSION_ID, "applied-pg-crash")
    assert mailbox is not None
    assert mailbox.disposition == "pending"
    assert await store.load_effect_slot(SESSION_ID, "effect-pg-crash") is None
    assert await store.load_transition_receipt(SESSION_ID, 0, "pg-crash") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "durable_disposition",
    [None, "applied", "rejected", "superseded"],
)
async def test_postgresql_disposition_requires_pending_admitted_command_and_rolls_back(
    tmp_path: Path,
    durable_disposition: str | None,
) -> None:
    store, owner = await _open_store("pg", tmp_path)
    if durable_disposition is not None:
        _seed_phase_b_pg_commands(
            store,
            "pg-invalid-disposition",
            disposition=durable_disposition,
        )
    fact_source_before = await store.load_session_fact_source(SESSION_ID)

    with pytest.raises(CommandDispositionConflictError):
        await store.commit_authoritative_uow(
            owner,
            _phase_b_pg_unit("pg-invalid-disposition", revision=0),
        )

    assert await store.load_operation_state(SESSION_ID, "run-phase-b-pg") is None
    assert await store.load_event_record(SESSION_ID, "1") is None
    assert await store.load_session_fact_source(SESSION_ID) == fact_source_before
    assert (
        await store.load_effect_slot(
            SESSION_ID,
            "effect-pg-invalid-disposition",
        )
        is None
    )
    assert (
        await store.load_transition_receipt(
            SESSION_ID,
            0,
            "pg-invalid-disposition",
        )
        is None
    )
    applied = await store.load_mailbox_slot(
        SESSION_ID,
        "applied-pg-invalid-disposition",
    )
    rejected = await store.load_mailbox_slot(
        SESSION_ID,
        "rejected-pg-invalid-disposition",
    )
    if durable_disposition is None:
        assert applied is None
        assert rejected is None
    else:
        assert applied is not None
        assert rejected is not None
        assert applied.disposition == durable_disposition
        assert rejected.disposition == durable_disposition


@pytest.mark.asyncio
async def test_postgresql_transition_snapshots_caller_json_before_waiting(
    tmp_path: Path,
) -> None:
    store, owner = await _open_store("pg", tmp_path)
    entered_transaction = asyncio.Event()
    release_transaction = asyncio.Event()
    original_with_transaction = store._with_transaction

    async def blocked_with_transaction(body: Any) -> object:
        entered_transaction.set()
        await release_transaction.wait()
        return await original_with_transaction(body)

    store._with_transaction = blocked_with_transaction  # type: ignore[method-assign]
    state_value: dict[str, object] = {"phase": "before"}
    fact_payload: dict[str, object] = {"text": "before"}
    unit = AuthoritativeUnitOfWork(
        event=None,
        session_state=SESSION_PAYLOAD,
        transition_id="pg-snapshot",
        state_cas=OperationStateCAS(
            run_id="run-phase-b-pg",
            revision=0,
            projection_epoch=0,
        ),
        state_value=state_value,
        facts=(
            EventRecord(
                event_id="fact-pg-snapshot",
                session_id=SESSION_ID,
                event_kind="finalized_thinking",
                payload=fact_payload,
                created_at=datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
            ),
        ),
    )

    commit_task = asyncio.create_task(store.commit_authoritative_uow(owner, unit))
    await entered_transaction.wait()
    state_value["phase"] = "after"
    fact_payload["text"] = "after"
    release_transaction.set()
    committed = await commit_task

    assert committed.state_version is not None
    assert committed.state_version.value == {"phase": "before"}
    assert committed.facts[0].payload == {"text": "before"}
    assert committed.transition_receipt is not None
    assert committed.transition_receipt.state_version.value == {"phase": "before"}
    assert committed.transition_receipt.facts[0].payload == {"text": "before"}

    retry = await store.commit_authoritative_uow(
        owner,
        AuthoritativeUnitOfWork(
            event=None,
            session_state=SESSION_PAYLOAD,
            transition_id="pg-snapshot",
            state_cas=OperationStateCAS(
                run_id="run-phase-b-pg",
                revision=0,
                projection_epoch=0,
            ),
            state_value={"phase": "before"},
            facts=(
                EventRecord(
                    event_id="fact-pg-snapshot",
                    session_id=SESSION_ID,
                    event_kind="finalized_thinking",
                    payload={"text": "before"},
                    created_at=datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
                ),
            ),
        ),
    )
    assert retry.idempotent is True
    assert retry.state_version == committed.state_version
    assert retry.facts == committed.facts


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
@pytest.mark.parametrize(
    "non_finite",
    [float("nan"), float("inf"), float("-inf")],
)
async def test_phase_b_stores_reject_non_finite_transition_json(
    store_kind: str,
    non_finite: float,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    with pytest.raises(ValueError, match="non-finite float"):
        await store.commit_authoritative_uow(
            owner,
            AuthoritativeUnitOfWork(
                event=None,
                session_state=SESSION_PAYLOAD,
                transition_id="non-finite",
                state_cas=OperationStateCAS(
                    run_id="run-non-finite",
                    revision=0,
                    projection_epoch=0,
                ),
                state_value={"number": non_finite},
            ),
        )

    assert await store.load_operation_state(SESSION_ID, "run-non-finite") is None
    assert (
        await store.load_transition_receipt(
            SESSION_ID,
            0,
            "non-finite",
        )
        is None
    )


async def _assert_mailbox_admission_advances_dispatch_generation(
    kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(kind, tmp_path)
    commands = (
        RuntimeCommand(
            command_id="command-steer",
            command_kind="user_steer",
            payload={"text": "inspect"},
        ),
        RuntimeCommand(
            command_id="command-allow",
            command_kind="approval_decision",
            payload={"approved": True, "request_id": "approval-1"},
        ),
        RuntimeCommand(
            command_id="command-deny",
            command_kind="approval_decision",
            payload={"approved": False, "request_id": "approval-2"},
        ),
        RuntimeCommand(
            command_id="command-interrupt",
            command_kind="interrupt",
        ),
        RuntimeCommand(
            command_id="command-cancel",
            command_kind="cancel",
        ),
    )

    admissions = [
        await store.admit_runtime_command(owner, command) for command in commands
    ]

    assert [admission.entry.admitted_session_seq for admission in admissions] == [
        "1",
        "2",
        "3",
        "4",
        "5",
    ]
    assert [
        admission.entry.admitted_dispatch_generation for admission in admissions
    ] == ["0", "0", "1", "2", "3"]
    assert [admission.mailbox_cut for admission in admissions] == [
        "0",
        "0",
        "1",
        "2",
        "3",
    ]
    assert all(not admission.idempotent for admission in admissions)

    snapshot = await store.load_runtime_command_mailbox(SESSION_ID)
    assert snapshot.mailbox_cut == "3"
    assert tuple(entry.command for entry in snapshot.entries) == commands
    assert [entry.admitted_session_seq for entry in snapshot.entries] == [
        "1",
        "2",
        "3",
        "4",
        "5",
    ]

    replay = await store.admit_runtime_command(owner, commands[2])
    assert replay.idempotent
    assert replay.entry == admissions[2].entry
    assert replay.mailbox_cut == "3"

    fact_source_before_conflict = await store.load_session_fact_source(SESSION_ID)
    with pytest.raises(RuntimeCommandAdmissionConflictError):
        await store.admit_runtime_command(
            owner,
            RuntimeCommand(
                command_id="command-deny",
                command_kind="approval_decision",
                payload={"approved": True, "request_id": "approval-2"},
            ),
        )
    assert (
        await store.load_session_fact_source(SESSION_ID) == fact_source_before_conflict
    )


@pytest.mark.asyncio
async def test_mailbox_admission_advances_dispatch_generation_sqlite(
    tmp_path: Path,
) -> None:
    await _assert_mailbox_admission_advances_dispatch_generation("sqlite", tmp_path)


@pytest.mark.asyncio
async def test_mailbox_admission_advances_dispatch_generation_postgresql(
    tmp_path: Path,
) -> None:
    await _assert_mailbox_admission_advances_dispatch_generation("pg", tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_mailbox_replay_rejects_type_changed_json_payload(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    await store.admit_runtime_command(
        owner,
        RuntimeCommand(
            command_id="command-type-sensitive",
            command_kind="user_steer",
            payload={"value": True},
        ),
    )

    with pytest.raises(RuntimeCommandAdmissionConflictError):
        await store.admit_runtime_command(
            owner,
            RuntimeCommand(
                command_id="command-type-sensitive",
                command_kind="user_steer",
                payload={"value": 1},
            ),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_runtime_command_disposition_preserves_admission_metadata(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    command = RuntimeCommand(
        command_id="command-preserved",
        command_kind="user_steer",
        payload={"text": "continue"},
    )
    admission = await store.admit_runtime_command(owner, command)

    await store.commit_authoritative_uow(
        owner,
        AuthoritativeUnitOfWork(
            event=None,
            session_state=SESSION_PAYLOAD,
            transition_id="transition-disposition-preserves-command",
            state_cas=OperationStateCAS(
                run_id="run-disposition-preserves-command",
                revision=0,
                projection_epoch=0,
            ),
            state_value={"status": "running"},
            dispositions=(AppliedCommandDisposition(command_id=command.command_id),),
        ),
    )

    assert (await store.load_runtime_command_mailbox(SESSION_ID)).entries == ()
    replay = await store.admit_runtime_command(owner, command)
    assert replay.idempotent
    assert replay.entry.command == command
    assert replay.entry.admitted_session_seq == admission.entry.admitted_session_seq
    assert (
        replay.entry.admitted_dispatch_generation
        == admission.entry.admitted_dispatch_generation
    )
    assert replay.entry.disposition == "applied"


@pytest.mark.asyncio
async def test_reconciliation_evidence_identity_conflict_is_rejected_postgresql(
    tmp_path: Path,
) -> None:
    store, owner = await _open_store("pg", tmp_path)
    evidence = EffectReconciliationEvidence(
        evidence_ref="pg-evidence",
        session_id=SESSION_ID,
        effect_id="pg-effect",
        attempt_id="pg-attempt",
        authorization_transition_id="pg-authorization",
        reconciliation_owner_epoch=owner.epoch,
        outcome=ReconciliationOutcome.COMPLETED,
        result={"content": "stable"},
    )

    assert (
        await store.record_effect_reconciliation_evidence(owner, evidence) == evidence
    )
    assert (
        await store.load_effect_reconciliation_evidence(
            SESSION_ID,
            evidence.evidence_ref,
        )
        == evidence
    )
    assert (
        await store.record_effect_reconciliation_evidence(owner, evidence) == evidence
    )
    with pytest.raises(RecoveryEvidenceConflictError):
        await store.record_effect_reconciliation_evidence(
            owner,
            replace(evidence, result={"content": "changed"}),
        )


@pytest.mark.asyncio
async def test_reconciliation_wrong_authorization_writes_nothing_postgresql(
    tmp_path: Path,
) -> None:
    store, owner = await _open_store("pg", tmp_path)
    prepare_id = "pg-reconciliation-prepare"
    effect_id = f"effect-{prepare_id}"
    attempt_id = f"attempt-{prepare_id}"
    authorization_id = "pg-reconciliation-authorize"
    _seed_phase_b_pg_commands(store, prepare_id)
    await store.commit_authoritative_uow(
        owner,
        _phase_b_pg_unit(prepare_id, revision=0),
    )
    await store.commit_authoritative_uow(
        owner,
        _phase_b_pg_dispatch_unit(
            authorization_id,
            revision=1,
            effect_id=effect_id,
            attempt_id=attempt_id,
            expected_mailbox_cut="0",
            state_value=_phase_d4_pg_state(
                effect_id=effect_id,
                attempt_id=attempt_id,
                authorization_transition_id=authorization_id,
            ),
        ),
    )
    unknown_state_value = _phase_d4_pg_state(
        effect_id=effect_id,
        attempt_id=attempt_id,
        authorization_transition_id=authorization_id,
        unknown_input_id="pg-indeterminate",
    )
    await store.commit_authoritative_uow(
        owner,
        AuthoritativeUnitOfWork(
            event=None,
            session_state=SESSION_PAYLOAD,
            transition_id="pg-indeterminate",
            state_cas=OperationStateCAS(
                run_id="run-phase-b-pg",
                revision=2,
                projection_epoch=0,
            ),
            state_value=unknown_state_value,
            effect_mutation=EffectMutation(
                effect_id=effect_id,
                attempt_id=attempt_id,
                expected_status=EffectStatus.DISPATCHED,
                status=EffectStatus.UNKNOWN,
                payload={"result": None},
            ),
        ),
    )
    evidence = EffectReconciliationEvidence(
        evidence_ref="pg-reconciliation-evidence",
        session_id=SESSION_ID,
        effect_id=effect_id,
        attempt_id=attempt_id,
        authorization_transition_id=authorization_id,
        reconciliation_owner_epoch=owner.epoch,
        outcome=ReconciliationOutcome.COMPLETED,
        result={"content": "recovered"},
    )
    await store.record_effect_reconciliation_evidence(owner, evidence)
    record = ReconciliationRecord(
        effect_id=effect_id,
        attempt_id=attempt_id,
        observed_outcome=ReconciliationOutcome.COMPLETED,
        evidence_ref=evidence.evidence_ref,
        actor_id="pg-recovery-worker",
        owner_epoch=owner.epoch,
        transition_id="pg-reconciliation",
    )
    mutation = EffectMutation(
        effect_id=effect_id,
        attempt_id=attempt_id,
        expected_status=EffectStatus.UNKNOWN,
        status=EffectStatus.COMPLETED,
        payload={
            "result": {"content": "recovered"},
            "reason_code": None,
            "reason_message": None,
        },
        reconciliation=record,
    )
    current_state = await store.load_operation_state(
        SESSION_ID,
        "run-phase-b-pg",
    )
    assert current_state is not None
    reconciled_state_value = state_value_with_reconciled_effect(
        current_state.value,
        evidence,
        record,
    )

    wrong = AuthoritativeUnitOfWork(
        event=None,
        session_state=SESSION_PAYLOAD,
        transition_id=record.transition_id,
        state_cas=current_state.cas,
        state_value=reconciled_state_value,
        effect_mutation=mutation,
        expected_reconciliation_authorization_transition_id="wrong-authorization",
        reconciliation_evidence_ref=evidence.evidence_ref,
    )
    with pytest.raises(EffectMutationConflictError):
        await store.commit_authoritative_uow(owner, wrong)
    unchanged = await store.load_operation_state(SESSION_ID, "run-phase-b-pg")
    assert unchanged == current_state
    effect = await store.load_effect_slot(SESSION_ID, effect_id)
    assert effect is not None
    assert effect.status == "unknown"
    assert (
        await store.load_transition_receipt(
            SESSION_ID,
            0,
            record.transition_id,
        )
        is None
    )

    tampered_runtime = dict(reconciled_state_value["_agentkit_runtime"])
    tampered_marker = dict(tampered_runtime["reconciled_effect"])
    tampered_marker["result"] = {"content": "tampered"}
    tampered_runtime["reconciled_effect"] = tampered_marker
    tampered_state_value = {
        **reconciled_state_value,
        "_agentkit_runtime": tampered_runtime,
    }
    with pytest.raises(
        EffectMutationConflictError,
        match="canonical evidence",
    ):
        await store.commit_authoritative_uow(
            owner,
            replace(
                wrong,
                state_value=tampered_state_value,
                expected_reconciliation_authorization_transition_id=authorization_id,
            ),
        )

    unit = replace(
        wrong,
        expected_reconciliation_authorization_transition_id=authorization_id,
    )
    committed = await store.commit_authoritative_uow(owner, unit)
    replayed = await store.commit_authoritative_uow(owner, unit)
    assert committed.state_version is not None
    assert replayed.idempotent is True
    assert replayed.state_version == committed.state_version


@pytest.mark.asyncio
async def test_takeover_waits_for_authoritative_executor_quiescence_row(
    tmp_path: Path,
) -> None:
    store, owner = await _open_store("pg", tmp_path)
    prepare_id = "pg-started-takeover-prepare"
    effect_id = f"effect-{prepare_id}"
    attempt_id = f"attempt-{prepare_id}"
    authorization_id = "pg-started-takeover-authorize"
    now = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    _seed_phase_b_pg_commands(store, prepare_id)
    await store.commit_authoritative_uow(
        owner,
        _phase_b_pg_unit(prepare_id, revision=0),
    )
    await store.commit_authoritative_uow(
        owner,
        _phase_b_pg_dispatch_unit(
            authorization_id,
            revision=1,
            effect_id=effect_id,
            attempt_id=attempt_id,
            expected_mailbox_cut="0",
            state_value=_phase_d4_pg_state(
                effect_id=effect_id,
                attempt_id=attempt_id,
                authorization_transition_id=authorization_id,
            ),
        ),
    )
    reserved = await store.reserve_executor_attempt(
        owner,
        effect_id=effect_id,
        attempt_id=attempt_id,
        authorization_transition_id=authorization_id,
        executor_id="pg-old-executor",
        lease_expires_at=now + timedelta(minutes=5),
    )
    await store.mark_executor_attempt_started(
        owner,
        effect_id=effect_id,
        attempt_id=attempt_id,
        authorization_transition_id=authorization_id,
        executor_id="pg-old-executor",
        claim_generation=reserved.claim_generation,
        now=now,
    )
    await store.commit_authoritative_uow(
        owner,
        AuthoritativeUnitOfWork(
            event=None,
            session_state=SESSION_PAYLOAD,
            transition_id="pg-started-indeterminate",
            state_cas=OperationStateCAS(
                run_id="run-phase-b-pg",
                revision=2,
                projection_epoch=0,
            ),
            state_value=_phase_d4_pg_state(
                effect_id=effect_id,
                attempt_id=attempt_id,
                authorization_transition_id=authorization_id,
                unknown_input_id="pg-started-indeterminate",
            ),
            effect_mutation=EffectMutation(
                effect_id=effect_id,
                attempt_id=attempt_id,
                expected_status=EffectStatus.DISPATCHED,
                status=EffectStatus.UNKNOWN,
                payload={"result": None},
            ),
        ),
    )
    takeover = OwnerAuthority(
        session_id=SESSION_ID,
        owner_id="pg-takeover-owner",
        epoch=owner.epoch + 1,
    )
    store._harness_pool.seed_owner(takeover)
    evidence = EffectReconciliationEvidence(
        evidence_ref="pg-started-evidence",
        session_id=SESSION_ID,
        effect_id=effect_id,
        attempt_id=attempt_id,
        authorization_transition_id=authorization_id,
        reconciliation_owner_epoch=takeover.epoch,
        outcome=ReconciliationOutcome.COMPLETED,
        result={"content": "recovered"},
    )
    await store.record_effect_reconciliation_evidence(takeover, evidence)
    record = ReconciliationRecord(
        effect_id=effect_id,
        attempt_id=attempt_id,
        observed_outcome=ReconciliationOutcome.COMPLETED,
        evidence_ref=evidence.evidence_ref,
        actor_id="pg-recovery-worker",
        owner_epoch=takeover.epoch,
        transition_id="pg-started-reconciliation",
    )
    mutation = EffectMutation(
        effect_id=effect_id,
        attempt_id=attempt_id,
        expected_status=EffectStatus.UNKNOWN,
        status=EffectStatus.COMPLETED,
        payload={
            "result": {"content": "recovered"},
            "reason_code": None,
            "reason_message": None,
        },
        reconciliation=record,
    )
    current_state = await store.load_operation_state(
        SESSION_ID,
        "run-phase-b-pg",
    )
    assert current_state is not None
    state_value = state_value_with_reconciled_effect(
        current_state.value,
        evidence,
        record,
    )
    unit = AuthoritativeUnitOfWork(
        event=None,
        session_state=SESSION_PAYLOAD,
        transition_id=record.transition_id,
        state_cas=current_state.cas,
        state_value=state_value,
        effect_mutation=mutation,
        expected_reconciliation_authorization_transition_id=authorization_id,
        reconciliation_evidence_ref=evidence.evidence_ref,
    )

    with pytest.raises(
        ExecutorAttemptConflictError,
        match="not durably quiescent",
    ):
        await store.commit_authoritative_uow(takeover, unit)
    assert (
        await store.load_transition_receipt(
            SESSION_ID,
            0,
            record.transition_id,
        )
        is None
    )
    quiescent = await store.mark_executor_attempt_quiescent(
        takeover,
        effect_id=effect_id,
        attempt_id=attempt_id,
        authorization_transition_id=authorization_id,
        executor_id="pg-old-executor",
        claim_generation=reserved.claim_generation,
        now=now,
        evidence_ref="pg-old-executor-stopped",
    )
    assert quiescent.status == "quiescent"
    committed = await store.commit_authoritative_uow(takeover, unit)
    assert committed.state_version is not None
    assert committed.state_version.revision == 4
