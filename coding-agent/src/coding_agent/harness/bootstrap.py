"""Pure in-memory bootstrap and authoritative UoW model for ADR-0076."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
import json
from threading import RLock
from typing import Any, Callable, Mapping, TypeVar
from uuid import uuid4


_TABLES = (
    "event_records",
    "session_run_state",
    "mailbox_dispositions",
    "effect_ledger",
    "operation_receipts",
)


class AuthoritativeTransactionError(RuntimeError):
    """Base error for writes rejected by the authoritative transaction."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
        self.rejected_inside = "authoritative_transaction"
        self.committed_mutation_count = 0


class StaleFenceError(AuthoritativeTransactionError):
    """Raised when owner/session/epoch authority does not match."""


class TargetOwnershipError(AuthoritativeTransactionError):
    """Raised when a write targets another session."""


class InjectedCommitFailure(RuntimeError):
    """Raised by the reference model to prove atomic rollback."""

    def __init__(self, failed_after: str) -> None:
        super().__init__(f"injected failure after {failed_after}")
        self.failed_after = failed_after


class NonAuthoritativeImportError(RuntimeError):
    """Raised when JSONL is offered as an authoritative input."""

    def __init__(self) -> None:
        super().__init__("jsonl_is_derived_export")
        self.code = "jsonl_is_derived_export"


@dataclass(frozen=True)
class SessionAllocation:
    session_id: str
    owner_id: str
    request_id: str


@dataclass(frozen=True)
class SessionFence:
    session_id: str
    owner_id: str
    epoch: int

    def as_dict(self) -> dict[str, str | int]:
        return {
            "session_id": self.session_id,
            "owner_id": self.owner_id,
            "epoch": self.epoch,
        }


@dataclass(frozen=True)
class EventRecord:
    session_id: str
    session_seq: int
    kind: str


@dataclass(frozen=True)
class RunState:
    session_id: str
    run_id: str
    status: str


@dataclass(frozen=True)
class MailboxDisposition:
    session_id: str
    lane: str
    sequence: int
    disposition: str


@dataclass(frozen=True)
class EffectRecord:
    session_id: str
    effect_id: int
    state: str


@dataclass(frozen=True)
class OperationReceipt:
    session_id: str
    client_key: str
    effect_id: int


@dataclass(frozen=True)
class CommitResult:
    transaction_count: int
    committed_tables: tuple[str, ...]


_ResultT = TypeVar("_ResultT")
_ErrorT = TypeVar("_ErrorT", bound=BaseException)


class HarnessStore:
    """Thread-safe semantic reference store with copy-on-write transactions."""

    def __init__(self, *, first_session_number: int = 1) -> None:
        if isinstance(first_session_number, bool) or not isinstance(
            first_session_number, int
        ):
            raise TypeError("first_session_number must be an integer")
        if first_session_number < 0:
            raise ValueError("first_session_number must be non-negative")
        self.next_session_number = first_session_number
        self._lock = RLock()
        self._allocations: dict[str, SessionAllocation] = {}
        self._fences: dict[str, SessionFence] = {}
        self._projection_epochs: dict[str, int] = {}
        self._event_records: dict[tuple[str, int], EventRecord] = {}
        self._run_states: dict[str, RunState] = {}
        self._mailbox: dict[tuple[str, int], MailboxDisposition] = {}
        self._effects: dict[int, EffectRecord] = {}
        self._receipts: dict[str, OperationReceipt] = {}

    def allocate_session(
        self, *, owner_id: str, request_id: str | None = None
    ) -> SessionAllocation:
        self._require_text(owner_id, "owner_id")
        allocation_request_id = request_id if request_id is not None else uuid4().hex
        self._require_text(allocation_request_id, "request_id")
        with self._lock:
            existing = self._allocations.get(allocation_request_id)
            if existing is not None:
                if existing.owner_id != owner_id:
                    raise ValueError("request_id is already bound to another owner")
                return existing
            session_id = f"session-{self.next_session_number}"
            self.next_session_number += 1
            allocation = SessionAllocation(
                session_id=session_id,
                owner_id=owner_id,
                request_id=allocation_request_id,
            )
            self._allocations[allocation_request_id] = allocation
            self._fences[session_id] = SessionFence(session_id, owner_id, 0)
            self._projection_epochs[session_id] = 0
            return allocation

    def session_fence(self, session_id: str) -> SessionFence:
        with self._lock:
            return self._fences[session_id]

    def projection_epoch(self, session_id: str) -> int:
        with self._lock:
            return self._projection_epochs[session_id]

    def commit_authoritative_uow(
        self,
        *,
        fence: Mapping[str, object],
        event_record: Mapping[str, object],
        state: Mapping[str, object] | None,
        mailbox: Mapping[str, object] | None,
        effect: Mapping[str, object] | None,
        receipt: Mapping[str, object] | None,
        fail_after: str | None = None,
    ) -> CommitResult:
        if fail_after is not None and fail_after not in _TABLES:
            raise ValueError(f"unknown failure point: {fail_after}")
        with self._lock:
            session_id = self._fence_session_id(fence)
            self._validate_payload_ownership(
                session_id, event_record, state, mailbox, effect, receipt
            )
            staged = self._stage_tables(
                session_id=session_id,
                event_record=event_record,
                state=state,
                mailbox=mailbox,
                effect=effect,
                receipt=receipt,
                fail_after=fail_after,
            )
            self._validate_fence(fence)
            self._validate_target_ownership(session_id, staged)
            (
                self._event_records,
                self._run_states,
                self._mailbox,
                self._effects,
                self._receipts,
            ) = staged
            return CommitResult(1, _TABLES)

    def _stage_tables(
        self,
        *,
        session_id: str,
        event_record: Mapping[str, object],
        state: Mapping[str, object] | None,
        mailbox: Mapping[str, object] | None,
        effect: Mapping[str, object] | None,
        receipt: Mapping[str, object] | None,
        fail_after: str | None,
    ) -> tuple[dict[Any, Any], ...]:
        tables: tuple[dict[Any, Any], ...] = (
            deepcopy(self._event_records),
            deepcopy(self._run_states),
            deepcopy(self._mailbox),
            deepcopy(self._effects),
            deepcopy(self._receipts),
        )
        records = (
            self._make_event(session_id, event_record),
            self._make_state(session_id, state),
            self._make_mailbox(session_id, mailbox),
            self._make_effect(session_id, effect),
            self._make_receipt(session_id, receipt),
        )
        for table_name, table, record in zip(_TABLES, tables, records, strict=True):
            if record is not None:
                key = self._record_key(record)
                if key in table:
                    raise ValueError(f"record already exists in {table_name}: {key}")
                table[key] = record
            if fail_after == table_name:
                raise InjectedCommitFailure(table_name)
        return tables

    def _validate_fence(self, fence: Mapping[str, object]) -> None:
        session_id = self._fence_session_id(fence)
        expected = self._fences.get(session_id)
        if expected is None or (
            fence.get("owner_id") != expected.owner_id
            or fence.get("epoch") != expected.epoch
        ):
            raise StaleFenceError("stale_epoch")

    @classmethod
    def _validate_payload_ownership(
        cls, session_id: str, *payloads: object
    ) -> None:
        seen: set[int] = set()
        for payload in payloads:
            cls._validate_session_references(session_id, payload, seen)

    @classmethod
    def _validate_session_references(
        cls, session_id: str, value: object, seen: set[int]
    ) -> None:
        if not isinstance(value, (Mapping, Sequence)) or isinstance(
            value, (str, bytes, bytearray)
        ):
            return
        identity = id(value)
        if identity in seen:
            return
        seen.add(identity)
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if key == "session_id" and nested != session_id:
                    raise TargetOwnershipError("cross_session_target")
                cls._validate_session_references(session_id, nested, seen)
            return
        for nested in value:
            cls._validate_session_references(session_id, nested, seen)

    @staticmethod
    def _validate_target_ownership(
        session_id: str, tables: tuple[dict[Any, Any], ...]
    ) -> None:
        if any(
            record.session_id != session_id
            for table in tables
            for record in table.values()
        ):
            raise TargetOwnershipError("cross_session_target")

    @staticmethod
    def _fence_session_id(fence: Mapping[str, object]) -> str:
        session_id = fence.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise StaleFenceError("stale_epoch")
        return session_id

    @classmethod
    def _make_event(
        cls, session_id: str, values: Mapping[str, object]
    ) -> EventRecord:
        return EventRecord(
            cls._target_session(values, session_id),
            cls._require_int(values, "session_seq"),
            cls._require_mapping_text(values, "kind"),
        )

    @classmethod
    def _make_state(
        cls, session_id: str, values: Mapping[str, object] | None
    ) -> RunState | None:
        if values is None:
            return None
        return RunState(
            cls._target_session(values, session_id),
            cls._require_mapping_text(values, "run_id"),
            cls._require_mapping_text(values, "status"),
        )

    @classmethod
    def _make_mailbox(
        cls, session_id: str, values: Mapping[str, object] | None
    ) -> MailboxDisposition | None:
        if values is None:
            return None
        return MailboxDisposition(
            cls._target_session(values, session_id),
            cls._require_mapping_text(values, "lane"),
            cls._require_int(values, "sequence"),
            cls._require_mapping_text(values, "disposition"),
        )

    @classmethod
    def _make_effect(
        cls, session_id: str, values: Mapping[str, object] | None
    ) -> EffectRecord | None:
        if values is None:
            return None
        return EffectRecord(
            cls._target_session(values, session_id),
            cls._require_int(values, "effect_id"),
            cls._require_mapping_text(values, "state"),
        )

    @classmethod
    def _make_receipt(
        cls, session_id: str, values: Mapping[str, object] | None
    ) -> OperationReceipt | None:
        if values is None:
            return None
        return OperationReceipt(
            cls._target_session(values, session_id),
            cls._require_mapping_text(values, "client_key"),
            cls._require_int(values, "effect_id"),
        )

    @staticmethod
    def _target_session(values: Mapping[str, object], default: str) -> str:
        target = values.get("session_id", default)
        if not isinstance(target, str) or not target:
            raise ValueError("session_id must be a non-empty string")
        return target

    @staticmethod
    def _record_key(record: object) -> object:
        if isinstance(record, EventRecord):
            return record.session_id, record.session_seq
        if isinstance(record, RunState):
            return record.run_id
        if isinstance(record, MailboxDisposition):
            return record.lane, record.sequence
        if isinstance(record, EffectRecord):
            return record.effect_id
        if isinstance(record, OperationReceipt):
            return record.client_key
        raise TypeError(f"unsupported record: {type(record).__name__}")

    @staticmethod
    def _require_text(value: object, name: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a non-empty string")

    @classmethod
    def _require_mapping_text(cls, values: Mapping[str, object], name: str) -> str:
        value = values.get(name)
        cls._require_text(value, name)
        assert isinstance(value, str)
        return value

    @staticmethod
    def _require_int(values: Mapping[str, object], name: str) -> int:
        value = values.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} must be an integer")
        return value

    def event_record(self, session_id: str, session_seq: int) -> EventRecord:
        with self._lock:
            return self._event_records[session_id, session_seq]

    def run_state(self, run_id: str) -> RunState:
        with self._lock:
            return self._run_states[run_id]

    def mailbox_item(self, lane: str, sequence: int) -> MailboxDisposition:
        with self._lock:
            return self._mailbox[lane, sequence]

    def effect(self, effect_id: int) -> EffectRecord:
        with self._lock:
            return self._effects[effect_id]

    def receipt(self, client_key: str) -> OperationReceipt:
        with self._lock:
            return self._receipts[client_key]

    def table_counts(self) -> dict[str, int]:
        with self._lock:
            return dict(
                zip(
                    _TABLES,
                    (
                        len(self._event_records),
                        len(self._run_states),
                        len(self._mailbox),
                        len(self._effects),
                        len(self._receipts),
                    ),
                    strict=True,
                )
            )

    def last_session_seq(self, session_id: str) -> int:
        with self._lock:
            sequences = [
                sequence
                for record_session, sequence in self._event_records
                if record_session == session_id
            ]
            return max(sequences, default=0)

    def export_jsonl(self, *, session_id: str) -> str:
        with self._lock:
            records = sorted(
                (
                    record
                    for record in self._event_records.values()
                    if record.session_id == session_id
                ),
                key=lambda record: record.session_seq,
            )
            return "".join(
                json.dumps(
                    {
                        "kind": record.kind,
                        "session_id": record.session_id,
                        "session_seq": str(record.session_seq),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
                for record in records
            )

    def restore_from_jsonl(self, *, session_id: str, jsonl: str) -> None:
        del session_id, jsonl
        raise NonAuthoritativeImportError

    @staticmethod
    def capture_error(
        error_type: type[_ErrorT],
        operation: Callable[..., _ResultT],
        /,
        **kwargs: Any,
    ) -> _ErrorT:
        try:
            operation(**kwargs)
        except error_type as error:
            return error
        raise AssertionError(f"{error_type.__name__} was not raised")
