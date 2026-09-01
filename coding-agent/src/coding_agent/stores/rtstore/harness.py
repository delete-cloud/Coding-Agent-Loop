"""Harness UoW, cursor, effect, and receipt types."""

from __future__ import annotations

import hashlib
import json
import math

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any, Final, cast

from agentkit.runtime.contracts import (
    AppliedCommandDisposition,
    CommandDisposition,
    CommitRef,
    CommittedFactNotice,
    EffectMutation,
    EffectStatus,
    ReconciliationRecord,
    ReconciliationOutcome,
    OperationStateCAS,
    OperationStateVersion,
    RuntimeCommand,
    RejectedCommandDisposition,
    SupersededCommandDisposition,
    TransitionReceipt,
)
from coding_agent.stores.rtstore.records import (
    AgentRunRecord,
    JSONObject,
)
from coding_agent.stores.rtstore.validate import (
    _require_datetime,
    _require_json_object,
    _require_non_empty,
)

DEFAULT_HARNESS_PROJECTION: Final[str] = "default"
_MAX_U64: Final[int] = (1 << 64) - 1


class AuthoritativeWriteRefusedError(RuntimeError):
    """Raised when a derived store is asked to accept a harness unit of work."""


class CursorEpochMismatchError(ValueError):
    """Raised when a delta/settled cursor is bound to the wrong projection or epoch."""


class StateVersionConflictError(RuntimeError):
    """Raised when the logical operation-state CAS precondition is stale."""


class TransitionFingerprintMismatchError(RuntimeError):
    """Raised when a transition receipt key is reused for a different mutation."""


class CommandDispositionConflictError(RuntimeError):
    """Raised when a transition dispositions a command that is not admitted."""


class RuntimeCommandAdmissionConflictError(RuntimeError):
    """Raised when a command identity is reused with different durable content."""


class EffectMutationConflictError(RuntimeError):
    """Raised when an explicit effect mutation does not match durable state."""


class InvalidDispatchAuthorizationError(ValueError):
    """Raised when a typed UoW has an invalid dispatch-cut precondition."""


class StaleMailboxCutError(RuntimeError):
    """Raised when dispatch authorization was computed against an old cut."""

    def __init__(
        self,
        *,
        expected_mailbox_cut: int,
        current_mailbox_cut: int,
    ) -> None:
        super().__init__(
            "dispatch authorization mailbox cut is stale: "
            f"expected {expected_mailbox_cut}, current {current_mailbox_cut}"
        )
        self.expected_mailbox_cut = expected_mailbox_cut
        self.current_mailbox_cut = current_mailbox_cut


class InvalidReconciliationPreconditionError(ValueError):
    """Raised when a reconciliation UoW omits its durable recovery binding."""


class RecoveryEvidenceConflictError(RuntimeError):
    """Raised when a durable recovery evidence identity changes content."""


class ExecutorAttemptConflictError(RuntimeError):
    """Raised when an executor-attempt state transition is not exact."""


class ExecutorAttemptStatus(StrEnum):
    AUTHORIZED_UNCLAIMED = "authorized_unclaimed"
    RESERVED = "reserved"
    STARTED = "started"
    QUIESCENT = "quiescent"


class KeyExpiredError(LookupError):
    """Raised when a cursor sits below the retention floor.

    Callers must either replay from ``retention_floor`` or accept a trusted handoff.
    """

    def __init__(
        self,
        *,
        session_id: str,
        retention_floor: str,
        cursor_seq: str,
    ) -> None:
        _require_non_empty("session_id", session_id)
        parse_u64(retention_floor, field_name="retention_floor")
        parse_u64(cursor_seq, field_name="cursor_seq")
        super().__init__(
            f"key expired for session {session_id}: cursor {cursor_seq} "
            f"is below retention floor {retention_floor}"
        )
        self.session_id = session_id
        self.retention_floor = retention_floor
        self.cursor_seq = cursor_seq


def format_u64(value: int) -> str:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("u64 must be an integer")
    if value < 0:
        raise ValueError("u64 must be non-negative")
    return str(value)


def parse_u64(value: str, *, field_name: str) -> int:
    if not isinstance(value, str) or not value.isdigit():
        raise ValueError(f"{field_name} must be a decimal u64 string")
    if len(value) > 1 and value.startswith("0"):
        raise ValueError(f"{field_name} must be a decimal u64 string")
    return int(value)


def assert_raw_cursor_not_expired(cursor: RawCursor, retention_floor: str) -> None:
    floor = parse_u64(retention_floor, field_name="retention_floor")
    cursor_seq = parse_u64(cursor.session_seq, field_name="session_seq")
    if cursor_seq + 1 < floor:
        raise KeyExpiredError(
            session_id=cursor.session_id,
            retention_floor=retention_floor,
            cursor_seq=cursor.session_seq,
        )


def assert_projection_binding(
    cursor: ProjectionCursor,
    state: SessionFactSourceState,
) -> None:
    if cursor.session_id != state.session_id:
        raise CursorEpochMismatchError(
            f"projection cursor session mismatch: {cursor.session_id}"
        )
    if cursor.projection != state.projection:
        raise CursorEpochMismatchError(
            f"projection cursor bound to projection {cursor.projection}, "
            f"current is {state.projection}"
        )
    if cursor.epoch != state.projection_epoch:
        raise CursorEpochMismatchError(
            f"projection cursor bound to epoch {cursor.epoch}, "
            f"current is {state.projection_epoch}"
        )


def assert_trusted_handoff(
    handoff: TrustedHandoff,
    state: SessionFactSourceState,
) -> None:
    if handoff.session_id != state.session_id:
        raise CursorEpochMismatchError(
            f"trusted handoff session mismatch: {handoff.session_id}"
        )
    if handoff.projection != state.projection:
        raise CursorEpochMismatchError(
            f"trusted handoff bound to projection {handoff.projection}, "
            f"current is {state.projection}"
        )
    if handoff.epoch != state.projection_epoch:
        raise CursorEpochMismatchError(
            f"trusted handoff bound to epoch {handoff.epoch}, "
            f"current is {state.projection_epoch}"
        )
    handoff_seq = parse_u64(handoff.session_seq, field_name="session_seq")
    current_seq = parse_u64(state.session_seq, field_name="session_seq")
    if handoff_seq > current_seq:
        raise CursorEpochMismatchError("trusted handoff is ahead of the physical log")


_EFFECT_STATUS_RANKS: Final[dict[str, int]] = {
    "prepared": 1,
    "dispatched": 2,
    "unknown": 3,
    "failed": 3,
    "completed": 4,
    "settled": 4,
}


def effect_status_rank(status: str) -> int:
    _require_non_empty("status", status)
    return _EFFECT_STATUS_RANKS.get(status, 0)


def effect_status_may_replace(*, current: str, incoming: str) -> bool:
    return effect_status_rank(incoming) >= effect_status_rank(current)


def receipt_generation_may_replace(*, current: str, incoming: str) -> bool:
    return parse_u64(incoming, field_name="generation") > parse_u64(
        current, field_name="generation"
    )


def stored_trusted_handoff(
    *,
    session_id: str,
    session_seq: int | None,
    epoch: int | None,
    projection: str | None,
    payload: JSONObject | None,
) -> TrustedHandoff | None:
    present = (
        session_seq is not None,
        epoch is not None,
        projection is not None,
        payload is not None,
    )
    if not any(present):
        return None
    if not all(present):
        raise TypeError("trusted handoff columns must be stored together")
    if session_seq is None or epoch is None or projection is None or payload is None:
        raise TypeError("trusted handoff columns must be stored together")
    return TrustedHandoff(
        session_id=session_id,
        session_seq=format_u64(session_seq),
        projection=projection,
        epoch=format_u64(epoch),
        payload=payload,
    )


@dataclass(frozen=True)
class EventRecord:
    event_id: str
    session_id: str
    event_kind: str
    payload: JSONObject
    created_at: datetime
    session_seq: str | None = None
    projection_epoch: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty("event_id", self.event_id)
        _require_non_empty("session_id", self.session_id)
        _require_non_empty("event_kind", self.event_kind)
        _require_json_object("payload", self.payload)
        _require_datetime("created_at", self.created_at)
        if self.session_seq is not None:
            parse_u64(self.session_seq, field_name="session_seq")
        if self.projection_epoch is not None:
            parse_u64(self.projection_epoch, field_name="projection_epoch")


@dataclass(frozen=True)
class MailboxDispositionSlot:
    slot_id: str
    lane: str
    disposition: str
    payload: JSONObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty("slot_id", self.slot_id)
        _require_non_empty("lane", self.lane)
        _require_non_empty("disposition", self.disposition)
        _require_json_object("payload", self.payload)


@dataclass(frozen=True)
class CommandMailboxEntry:
    command: RuntimeCommand
    admitted_session_seq: str
    admitted_dispatch_generation: str
    disposition: str

    def __post_init__(self) -> None:
        if not isinstance(self.command, RuntimeCommand):
            raise TypeError("command must be a RuntimeCommand")
        parse_u64(self.admitted_session_seq, field_name="admitted_session_seq")
        parse_u64(
            self.admitted_dispatch_generation,
            field_name="admitted_dispatch_generation",
        )
        _require_non_empty("disposition", self.disposition)


@dataclass(frozen=True)
class RuntimeCommandAdmission:
    entry: CommandMailboxEntry
    mailbox_cut: str
    idempotent: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.entry, CommandMailboxEntry):
            raise TypeError("entry must be a CommandMailboxEntry")
        mailbox_cut = parse_u64(self.mailbox_cut, field_name="mailbox_cut")
        admitted_generation = parse_u64(
            self.entry.admitted_dispatch_generation,
            field_name="admitted_dispatch_generation",
        )
        if admitted_generation > mailbox_cut:
            raise ValueError("entry dispatch generation cannot exceed mailbox_cut")
        if not isinstance(self.idempotent, bool):
            raise TypeError("idempotent must be a bool")


@dataclass(frozen=True)
class CommandMailboxSnapshot:
    entries: tuple[CommandMailboxEntry, ...]
    mailbox_cut: str

    def __post_init__(self) -> None:
        entries = tuple(self.entries)
        if any(not isinstance(entry, CommandMailboxEntry) for entry in entries):
            raise TypeError("entries must contain CommandMailboxEntry values")
        command_ids = [entry.command.command_id for entry in entries]
        if len(command_ids) != len(set(command_ids)):
            raise ValueError("entries must contain unique command identities")
        sequences = [
            parse_u64(entry.admitted_session_seq, field_name="admitted_session_seq")
            for entry in entries
        ]
        if sequences != sorted(sequences):
            raise ValueError("entries must be ordered by admitted_session_seq")
        mailbox_cut = parse_u64(self.mailbox_cut, field_name="mailbox_cut")
        if any(
            parse_u64(
                entry.admitted_dispatch_generation,
                field_name="admitted_dispatch_generation",
            )
            > mailbox_cut
            for entry in entries
        ):
            raise ValueError("entry dispatch generation cannot exceed mailbox_cut")
        object.__setattr__(self, "entries", entries)


@dataclass(frozen=True)
class EffectLedgerSlot:
    effect_id: str
    status: str
    payload: JSONObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty("effect_id", self.effect_id)
        _require_non_empty("status", self.status)
        _require_json_object("payload", self.payload)


@dataclass(frozen=True)
class EffectReconciliationEvidence:
    evidence_ref: str
    session_id: str
    effect_id: str
    attempt_id: str
    authorization_transition_id: str
    reconciliation_owner_epoch: int
    outcome: ReconciliationOutcome
    result: object
    reason_code: str | None = None
    reason_message: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "evidence_ref",
            "session_id",
            "effect_id",
            "attempt_id",
            "authorization_transition_id",
        ):
            _require_non_empty(field_name, getattr(self, field_name))
        if (
            isinstance(self.reconciliation_owner_epoch, bool)
            or not isinstance(self.reconciliation_owner_epoch, int)
            or self.reconciliation_owner_epoch <= 0
        ):
            raise ValueError("reconciliation_owner_epoch must be a positive integer")
        if not isinstance(self.outcome, ReconciliationOutcome):
            object.__setattr__(
                self,
                "outcome",
                ReconciliationOutcome(self.outcome),
            )
        if self.outcome is ReconciliationOutcome.COMPLETED:
            if self.reason_code is not None or self.reason_message is not None:
                raise ValueError("completed evidence cannot carry a failure reason")
        else:
            if self.reason_code is None or self.reason_message is None:
                raise ValueError("failed evidence requires a stable reason")
            _require_non_empty("reason_code", self.reason_code)
            if not self.reason_message.strip():
                raise ValueError("reason_message must not be blank")
        object.__setattr__(self, "result", _plain_json(self.result))

    def payload(self) -> JSONObject:
        return {
            "evidence_ref": self.evidence_ref,
            "session_id": self.session_id,
            "effect_id": self.effect_id,
            "attempt_id": self.attempt_id,
            "authorization_transition_id": self.authorization_transition_id,
            "reconciliation_owner_epoch": self.reconciliation_owner_epoch,
            "outcome": self.outcome.value,
            "result": self.result,
            "reason_code": self.reason_code,
            "reason_message": self.reason_message,
        }


def reconciled_effect_input_id(record: ReconciliationRecord) -> str:
    if not isinstance(record, ReconciliationRecord):
        raise TypeError("record must be a ReconciliationRecord")
    return f"{record.transition_id}:settled"


def state_value_with_reconciled_effect(
    current_value: Mapping[str, Any],
    evidence: EffectReconciliationEvidence,
    record: ReconciliationRecord,
) -> JSONObject:
    if not isinstance(evidence, EffectReconciliationEvidence):
        raise TypeError("evidence must be EffectReconciliationEvidence")
    if not isinstance(record, ReconciliationRecord):
        raise TypeError("record must be a ReconciliationRecord")
    if (
        evidence.effect_id != record.effect_id
        or evidence.attempt_id != record.attempt_id
        or evidence.reconciliation_owner_epoch != record.owner_epoch
        or evidence.outcome is not record.observed_outcome
        or evidence.evidence_ref != record.evidence_ref
    ):
        raise ValueError("evidence and reconciliation record do not match")
    state_value = cast(JSONObject, _plain_json(current_value))
    raw_runtime = state_value.get("_agentkit_runtime")
    if not isinstance(raw_runtime, dict):
        raise ValueError("reconciliation requires committed runtime state")
    runtime_state = dict(raw_runtime)
    raw_unknown = runtime_state.get("unknown_effect")
    if not isinstance(raw_unknown, dict):
        raise ValueError("reconciliation requires a committed unknown effect")
    unknown = dict(raw_unknown)
    raw_active = runtime_state.get("active_effect_authorization")
    pending_plans = runtime_state.get("pending_effect_plans")
    if not isinstance(raw_active, dict):
        raise ValueError("reconciliation requires retained authorization")
    if not isinstance(pending_plans, list | tuple) or not pending_plans:
        raise ValueError("reconciliation requires a retained effect plan")
    active = dict(raw_active)
    authorization_fields = (
        "effect_id",
        "attempt_id",
        "tool_call_id",
        "tool_name",
        "authorization_transition_id",
        "dispatch_owner_epoch",
    )
    if any(
        unknown.get(field_name) != active.get(field_name)
        for field_name in authorization_fields
    ):
        raise ValueError("unknown effect does not match retained authorization")
    indeterminate_input_id = unknown.get("indeterminate_input_id")
    if not isinstance(indeterminate_input_id, str) or not indeterminate_input_id:
        raise ValueError("unknown effect is missing its consume-once input")
    if (
        unknown.get("effect_id") != evidence.effect_id
        or unknown.get("attempt_id") != evidence.attempt_id
        or unknown.get("authorization_transition_id")
        != evidence.authorization_transition_id
    ):
        raise ValueError("unknown effect does not match reconciliation evidence")
    runtime_state["reconciled_effect"] = {
        **unknown,
        "reconciliation_transition_id": record.transition_id,
        "evidence_ref": evidence.evidence_ref,
        "reconciliation_owner_epoch": evidence.reconciliation_owner_epoch,
        "reconciled_input_id": reconciled_effect_input_id(record),
        "outcome": evidence.outcome.value,
        "result": evidence.result,
        "reason_code": evidence.reason_code,
        "reason_message": evidence.reason_message,
    }
    state_value["_agentkit_runtime"] = runtime_state
    return state_value


@dataclass(frozen=True)
class ExecutorAttemptRecord:
    session_id: str
    effect_id: str
    attempt_id: str
    authorization_transition_id: str
    dispatch_owner_epoch: int
    status: ExecutorAttemptStatus
    executor_id: str | None = None
    claim_generation: int = 0
    reservation_lease_expires_at: datetime | None = None
    quiescence_evidence_ref: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "session_id",
            "effect_id",
            "attempt_id",
            "authorization_transition_id",
            "status",
        ):
            _require_non_empty(field_name, getattr(self, field_name))
        if not isinstance(self.status, ExecutorAttemptStatus):
            object.__setattr__(self, "status", ExecutorAttemptStatus(self.status))
        if (
            isinstance(self.dispatch_owner_epoch, bool)
            or not isinstance(self.dispatch_owner_epoch, int)
            or self.dispatch_owner_epoch <= 0
        ):
            raise ValueError("dispatch_owner_epoch must be a positive integer")
        if (
            isinstance(self.claim_generation, bool)
            or not isinstance(self.claim_generation, int)
            or self.claim_generation < 0
        ):
            raise ValueError("claim_generation must be a non-negative integer")
        if self.status is ExecutorAttemptStatus.AUTHORIZED_UNCLAIMED:
            if (
                self.executor_id is not None
                or self.claim_generation != 0
                or self.reservation_lease_expires_at is not None
                or self.quiescence_evidence_ref is not None
            ):
                raise ValueError("authorized_unclaimed attempt cannot carry a claim")
            return
        if self.executor_id is None:
            raise ValueError("claimed executor attempt requires executor_id")
        _require_non_empty("executor_id", self.executor_id)
        if self.claim_generation <= 0:
            raise ValueError("claimed executor attempt requires claim_generation")
        if self.reservation_lease_expires_at is None:
            raise ValueError("claimed executor attempt requires reservation lease")
        _require_datetime(
            "reservation_lease_expires_at",
            self.reservation_lease_expires_at,
        )
        if self.status is ExecutorAttemptStatus.QUIESCENT:
            if self.quiescence_evidence_ref is None:
                raise ValueError("quiescent executor attempt requires evidence")
            _require_non_empty(
                "quiescence_evidence_ref",
                self.quiescence_evidence_ref,
            )
        elif self.quiescence_evidence_ref is not None:
            raise ValueError("only quiescent executor attempts carry evidence")

    def payload(self) -> JSONObject:
        return {
            "session_id": self.session_id,
            "effect_id": self.effect_id,
            "attempt_id": self.attempt_id,
            "authorization_transition_id": self.authorization_transition_id,
            "dispatch_owner_epoch": self.dispatch_owner_epoch,
            "status": self.status.value,
            "executor_id": self.executor_id,
            "claim_generation": self.claim_generation,
            "reservation_lease_expires_at": (
                None
                if self.reservation_lease_expires_at is None
                else self.reservation_lease_expires_at.isoformat()
            ),
            "quiescence_evidence_ref": self.quiescence_evidence_ref,
        }


@dataclass(frozen=True)
class OperationReceiptSlot:
    receipt_id: str
    generation: str
    payload: JSONObject = field(default_factory=dict)
    compensation_effect_id: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty("receipt_id", self.receipt_id)
        parse_u64(self.generation, field_name="generation")
        _require_json_object("payload", self.payload)
        if self.compensation_effect_id is not None:
            _require_non_empty("compensation_effect_id", self.compensation_effect_id)


@dataclass(frozen=True)
class AuthoritativeUnitOfWork:
    event: EventRecord | None
    session_state: JSONObject
    mailbox: MailboxDispositionSlot | None = None
    effect: EffectLedgerSlot | None = None
    receipt: OperationReceiptSlot | None = None
    run_state: AgentRunRecord | None = None
    require_settled_parent_run_id: str | None = None
    require_unsettled_root_run_id: str | None = None
    transition_id: str | None = None
    state_cas: OperationStateCAS | None = None
    state_value: Mapping[str, Any] | None = None
    facts: tuple[EventRecord, ...] = ()
    dispositions: tuple[CommandDisposition, ...] = ()
    effect_mutation: EffectMutation | None = None
    expected_mailbox_cut: str | None = None
    expected_reconciliation_authorization_transition_id: str | None = None
    reconciliation_evidence_ref: str | None = None

    def __post_init__(self) -> None:
        if self.event is not None and not isinstance(self.event, EventRecord):
            raise TypeError("event must be an EventRecord or None")
        _require_json_object("session_state", self.session_state)
        if self.mailbox is not None and not isinstance(
            self.mailbox, MailboxDispositionSlot
        ):
            raise TypeError("mailbox must be a MailboxDispositionSlot")
        if self.effect is not None and not isinstance(self.effect, EffectLedgerSlot):
            raise TypeError("effect must be an EffectLedgerSlot")
        if self.receipt is not None and not isinstance(
            self.receipt, OperationReceiptSlot
        ):
            raise TypeError("receipt must be an OperationReceiptSlot")
        if self.run_state is not None and not isinstance(
            self.run_state, AgentRunRecord
        ):
            raise TypeError("run_state must be an AgentRunRecord")
        if self.require_settled_parent_run_id is not None:
            _require_non_empty(
                "require_settled_parent_run_id",
                self.require_settled_parent_run_id,
            )
        if self.require_unsettled_root_run_id is not None:
            _require_non_empty(
                "require_unsettled_root_run_id",
                self.require_unsettled_root_run_id,
            )
            if (
                self.run_state is not None
                and self.run_state.run_id != self.require_unsettled_root_run_id
            ):
                raise ValueError(
                    "root settlement run_state does not match required run"
                )
        facts = tuple(self.facts)
        if any(not isinstance(fact, EventRecord) for fact in facts):
            raise TypeError("facts must contain EventRecord values")
        object.__setattr__(self, "facts", facts)
        dispositions = tuple(self.dispositions)
        disposition_types = (
            AppliedCommandDisposition,
            RejectedCommandDisposition,
            SupersededCommandDisposition,
        )
        if any(
            not isinstance(disposition, disposition_types)
            for disposition in dispositions
        ):
            raise TypeError("dispositions must contain typed command dispositions")
        command_ids = [disposition.command_id for disposition in dispositions]
        if len(command_ids) != len(set(command_ids)):
            raise ValueError("a transition may disposition each command at most once")
        object.__setattr__(self, "dispositions", dispositions)
        if self.effect_mutation is not None and not isinstance(
            self.effect_mutation,
            EffectMutation,
        ):
            raise TypeError("effect_mutation must be an EffectMutation")
        is_dispatch_authorization = (
            self.effect_mutation is not None
            and self.effect_mutation.expected_status is EffectStatus.PREPARED
            and self.effect_mutation.status is EffectStatus.DISPATCHED
        )
        if is_dispatch_authorization and self.expected_mailbox_cut is None:
            raise InvalidDispatchAuthorizationError(
                "expected_mailbox_cut is required for PREPARED -> DISPATCHED"
            )
        if not is_dispatch_authorization and self.expected_mailbox_cut is not None:
            raise InvalidDispatchAuthorizationError(
                "expected_mailbox_cut is forbidden outside PREPARED -> DISPATCHED"
            )
        if self.expected_mailbox_cut is not None:
            try:
                parsed_mailbox_cut = parse_u64(
                    self.expected_mailbox_cut,
                    field_name="expected_mailbox_cut",
                )
            except ValueError as error:
                raise InvalidDispatchAuthorizationError(str(error)) from error
            if parsed_mailbox_cut > _MAX_U64:
                raise InvalidDispatchAuthorizationError(
                    "expected_mailbox_cut must be a decimal u64 string"
                )

        is_reconciliation = (
            self.effect_mutation is not None
            and self.effect_mutation.expected_status is EffectStatus.UNKNOWN
            and self.effect_mutation.status
            in {EffectStatus.COMPLETED, EffectStatus.FAILED}
            and self.effect_mutation.reconciliation is not None
        )
        reconciliation_fields = (
            self.expected_reconciliation_authorization_transition_id,
            self.reconciliation_evidence_ref,
        )
        if is_reconciliation and any(value is None for value in reconciliation_fields):
            raise InvalidReconciliationPreconditionError(
                "reconciliation requires retained authorization and durable evidence"
            )
        if not is_reconciliation and any(
            value is not None for value in reconciliation_fields
        ):
            raise InvalidReconciliationPreconditionError(
                "reconciliation preconditions are forbidden outside UNKNOWN settlement"
            )
        if self.expected_reconciliation_authorization_transition_id is not None:
            _require_non_empty(
                "expected_reconciliation_authorization_transition_id",
                self.expected_reconciliation_authorization_transition_id,
            )
        if self.reconciliation_evidence_ref is not None:
            _require_non_empty(
                "reconciliation_evidence_ref",
                self.reconciliation_evidence_ref,
            )
            if (
                self.effect_mutation is not None
                and self.effect_mutation.reconciliation is not None
                and self.effect_mutation.reconciliation.evidence_ref
                != self.reconciliation_evidence_ref
            ):
                raise InvalidReconciliationPreconditionError(
                    "reconciliation evidence_ref must match the mutation record"
                )
        if self.transition_id is None:
            if self.event is None:
                raise ValueError("legacy unit of work requires an event")
            if (
                self.state_cas is not None
                or self.state_value is not None
                or facts
                or dispositions
                or self.effect_mutation is not None
            ):
                raise ValueError(
                    "transition fields require transition_id, state_cas, and state_value"
                )
        else:
            _require_non_empty("transition_id", self.transition_id)
            if not isinstance(self.state_cas, OperationStateCAS):
                raise TypeError("transition state_cas must be an OperationStateCAS")
            if self.state_value is None or not isinstance(self.state_value, Mapping):
                raise TypeError("transition state_value must be a mapping")
            if any(not isinstance(key, str) for key in self.state_value):
                raise TypeError("transition state_value keys must be strings")
            if self.event is not None:
                raise ValueError("transition facts must use facts, not legacy event")
            if (
                self.mailbox is not None
                or self.effect is not None
                or self.receipt is not None
                or self.run_state is not None
                or self.require_settled_parent_run_id is not None
                or self.require_unsettled_root_run_id is not None
            ):
                raise ValueError(
                    "typed transitions cannot mix legacy unit-of-work mutations"
                )
            if (
                self.effect_mutation is not None
                and self.effect_mutation.reconciliation is not None
                and self.effect_mutation.reconciliation.transition_id
                != self.transition_id
            ):
                raise ValueError(
                    "reconciliation transition_id must match the unit of work"
                )

        for event in ((self.event,) if self.event is not None else ()) + facts:
            if event.session_seq is not None:
                raise ValueError("event.session_seq is allocated by the store")
            if event.projection_epoch is not None:
                raise ValueError("event.projection_epoch is allocated by the store")

    @property
    def is_transition(self) -> bool:
        return self.transition_id is not None


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("transition mutation mapping keys must be strings")
        return {key: _plain_json(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain_json(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("transition mutation contains a non-finite float")
    if value is None or isinstance(value, bool | int | float | str):
        return value
    raise TypeError("transition mutation contains a non-JSON value")


def runtime_command_mailbox_payload(command: RuntimeCommand) -> JSONObject:
    if not isinstance(command, RuntimeCommand):
        raise TypeError("command must be a RuntimeCommand")
    return {
        "runtime_command_kind": command.command_kind,
        "runtime_command_payload": cast(JSONObject, _plain_json(command.payload)),
    }


def runtime_command_mailbox_payloads_equal(
    left: Mapping[str, object],
    right: Mapping[str, object],
) -> bool:
    def canonical(value: Mapping[str, object]) -> str:
        return json.dumps(
            _plain_json(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )

    return canonical(left) == canonical(right)


def runtime_command_from_mailbox_payload(
    *,
    command_id: str,
    payload: Mapping[str, object],
) -> RuntimeCommand:
    _require_non_empty("command_id", command_id)
    command_kind = payload.get("runtime_command_kind")
    command_payload = payload.get("runtime_command_payload")
    if not isinstance(command_kind, str) or not command_kind:
        raise ValueError("runtime command mailbox payload is missing command_kind")
    if not isinstance(command_payload, Mapping):
        raise TypeError(
            "runtime command mailbox payload must contain an object payload"
        )
    return RuntimeCommand(
        command_id=command_id,
        command_kind=command_kind,
        payload=command_payload,
    )


def runtime_command_invalidates_dispatch(command: RuntimeCommand) -> bool:
    if not isinstance(command, RuntimeCommand):
        raise TypeError("command must be a RuntimeCommand")
    if command.command_kind in {"cancel", "interrupt"}:
        return True
    if command.command_kind != "approval_decision":
        return False
    approved = command.payload.get("approved")
    if not isinstance(approved, bool):
        raise TypeError("approval_decision payload must contain boolean approved")
    return not approved


def snapshot_transition_unit(
    unit: AuthoritativeUnitOfWork,
) -> AuthoritativeUnitOfWork:
    """Detach every typed-transition JSON value from caller-owned objects."""

    if not unit.is_transition:
        return unit
    if unit.state_value is None:
        raise ValueError("typed transition is incomplete")
    facts = tuple(
        replace(
            fact,
            payload=cast(JSONObject, _plain_json(fact.payload)),
        )
        for fact in unit.facts
    )
    effect_mutation = (
        None
        if unit.effect_mutation is None
        else replace(
            unit.effect_mutation,
            payload=cast(
                Mapping[str, Any],
                _plain_json(unit.effect_mutation.payload),
            ),
        )
    )
    return AuthoritativeUnitOfWork(
        event=None,
        session_state=cast(JSONObject, _plain_json(unit.session_state)),
        transition_id=unit.transition_id,
        state_cas=unit.state_cas,
        state_value=cast(Mapping[str, Any], _plain_json(unit.state_value)),
        facts=facts,
        dispositions=unit.dispositions,
        effect_mutation=effect_mutation,
        expected_mailbox_cut=unit.expected_mailbox_cut,
        expected_reconciliation_authorization_transition_id=(
            unit.expected_reconciliation_authorization_transition_id
        ),
        reconciliation_evidence_ref=unit.reconciliation_evidence_ref,
    )


def mailbox_slot_from_disposition(
    disposition: CommandDisposition,
) -> MailboxDispositionSlot:
    if isinstance(disposition, AppliedCommandDisposition):
        payload: JSONObject = {}
    elif isinstance(disposition, RejectedCommandDisposition):
        payload = {"reason_code": disposition.reason_code}
    elif isinstance(disposition, SupersededCommandDisposition):
        payload = {
            "superseded_by_command_id": disposition.superseded_by_command_id,
        }
    else:
        raise TypeError("disposition must be a typed command disposition")
    return MailboxDispositionSlot(
        slot_id=disposition.command_id,
        lane="runtime",
        disposition=disposition.kind.value,
        payload=payload,
    )


def effect_slot_from_mutation(mutation: EffectMutation) -> EffectLedgerSlot:
    mutation_payload = cast(JSONObject, _plain_json(mutation.payload))
    payload: JSONObject = {
        **mutation_payload,
        "attempt_id": mutation.attempt_id,
    }
    if mutation.reconciliation is not None:
        payload["reconciliation"] = {
            "effect_id": mutation.reconciliation.effect_id,
            "attempt_id": mutation.reconciliation.attempt_id,
            "observed_outcome": mutation.reconciliation.observed_outcome.value,
            "evidence_ref": mutation.reconciliation.evidence_ref,
            "actor_id": mutation.reconciliation.actor_id,
            "owner_epoch": mutation.reconciliation.owner_epoch,
            "transition_id": mutation.reconciliation.transition_id,
        }
    return EffectLedgerSlot(
        effect_id=mutation.effect_id,
        status=mutation.status.value,
        payload=payload,
    )


def _disposition_fingerprint_value(
    disposition: CommandDisposition,
) -> dict[str, object]:
    slot = mailbox_slot_from_disposition(disposition)
    return {
        "command_id": disposition.command_id,
        "kind": disposition.kind.value,
        "payload": slot.payload,
    }


def _effect_fingerprint_value(mutation: EffectMutation | None) -> object:
    if mutation is None:
        return None
    reconciliation = mutation.reconciliation
    return {
        "effect_id": mutation.effect_id,
        "attempt_id": mutation.attempt_id,
        "expected_status": (
            None if mutation.expected_status is None else mutation.expected_status.value
        ),
        "status": mutation.status.value,
        "payload": _plain_json(mutation.payload),
        "reconciliation": (
            None
            if reconciliation is None
            else {
                "effect_id": reconciliation.effect_id,
                "attempt_id": reconciliation.attempt_id,
                "observed_outcome": reconciliation.observed_outcome.value,
                "evidence_ref": reconciliation.evidence_ref,
                "actor_id": reconciliation.actor_id,
                "owner_epoch": reconciliation.owner_epoch,
                "transition_id": reconciliation.transition_id,
            }
        ),
    }


def transition_mutation_fingerprint(unit: AuthoritativeUnitOfWork) -> str:
    if not unit.is_transition or unit.state_cas is None or unit.state_value is None:
        raise ValueError("mutation fingerprint requires a typed transition")
    mutation = {
        "state": {
            "run_id": unit.state_cas.run_id,
            "revision": unit.state_cas.revision + 1,
            "projection_epoch": unit.state_cas.projection_epoch,
            "value": _plain_json(unit.state_value),
        },
        "facts": [
            {
                "event_id": fact.event_id,
                "session_id": fact.session_id,
                "event_kind": fact.event_kind,
                "payload": _plain_json(fact.payload),
                "created_at": fact.created_at.isoformat(),
            }
            for fact in unit.facts
        ],
        "dispositions": [
            _disposition_fingerprint_value(disposition)
            for disposition in unit.dispositions
        ],
        "effect_mutation": _effect_fingerprint_value(unit.effect_mutation),
        "expected_mailbox_cut": unit.expected_mailbox_cut,
        "expected_reconciliation_authorization_transition_id": (
            unit.expected_reconciliation_authorization_transition_id
        ),
        "reconciliation_evidence_ref": unit.reconciliation_evidence_ref,
    }
    canonical = json.dumps(
        mutation,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def transition_commit_payload(
    *,
    state_version: OperationStateVersion,
    facts: tuple[EventRecord, ...],
    raw_cursor: RawCursor,
) -> JSONObject:
    return {
        "state_version": {
            "run_id": state_version.run_id,
            "revision": state_version.revision,
            "projection_epoch": state_version.projection_epoch,
            "commit_ref": {
                "transition_id": state_version.commit_ref.transition_id,
                "fact_seq_start": state_version.commit_ref.fact_seq_start,
                "fact_seq_end": state_version.commit_ref.fact_seq_end,
            },
            "value": cast(JSONObject, _plain_json(state_version.value)),
        },
        "facts": [
            {
                "event_id": fact.event_id,
                "session_id": fact.session_id,
                "event_kind": fact.event_kind,
                "payload": fact.payload,
                "created_at": fact.created_at.isoformat(),
                "session_seq": fact.session_seq,
                "projection_epoch": fact.projection_epoch,
            }
            for fact in facts
        ],
        "raw_cursor": {
            "session_id": raw_cursor.session_id,
            "session_seq": raw_cursor.session_seq,
        },
    }


def _required_mapping(
    payload: Mapping[str, object],
    key: str,
    *,
    context: str,
) -> Mapping[str, object]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise TypeError(f"{context} must include object {key}")
    if any(not isinstance(item_key, str) for item_key in value):
        raise TypeError(f"{context}.{key} keys must be strings")
    return cast(Mapping[str, object], value)


def _required_payload_str(
    payload: Mapping[str, object],
    key: str,
    *,
    context: str,
) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise TypeError(f"{context} must include non-empty string {key}")
    return value


def _required_payload_int(
    payload: Mapping[str, object],
    key: str,
    *,
    context: str,
) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{context} must include integer {key}")
    return value


def transition_commit_from_payload(
    *,
    session_id: str,
    transition_id: str,
    projection_epoch: int,
    mutation_fingerprint: str,
    payload: Mapping[str, object],
) -> tuple[
    OperationStateVersion,
    tuple[EventRecord, ...],
    TransitionReceipt,
    RawCursor,
]:
    state_payload = _required_mapping(payload, "state_version", context="transition")
    commit_payload = _required_mapping(
        state_payload,
        "commit_ref",
        context="transition state",
    )
    commit_ref = CommitRef(
        transition_id=_required_payload_str(
            commit_payload,
            "transition_id",
            context="transition commit_ref",
        ),
        fact_seq_start=cast(str | None, commit_payload.get("fact_seq_start")),
        fact_seq_end=cast(str | None, commit_payload.get("fact_seq_end")),
    )
    if commit_ref.transition_id != transition_id:
        raise ValueError("stored transition result has the wrong transition_id")
    value = _required_mapping(state_payload, "value", context="transition state")
    state_version = OperationStateVersion(
        run_id=_required_payload_str(
            state_payload,
            "run_id",
            context="transition state",
        ),
        revision=_required_payload_int(
            state_payload,
            "revision",
            context="transition state",
        ),
        projection_epoch=_required_payload_int(
            state_payload,
            "projection_epoch",
            context="transition state",
        ),
        commit_ref=commit_ref,
        value=value,
    )
    if state_version.projection_epoch != projection_epoch:
        raise ValueError("stored transition result has the wrong projection_epoch")
    raw_facts = payload.get("facts")
    if not isinstance(raw_facts, list):
        raise TypeError("transition result must include facts list")
    facts: list[EventRecord] = []
    for raw_fact in raw_facts:
        if not isinstance(raw_fact, Mapping):
            raise TypeError("transition result facts must contain objects")
        fact_payload = _required_mapping(
            raw_fact,
            "payload",
            context="transition fact",
        )
        fact = EventRecord(
            event_id=_required_payload_str(
                raw_fact,
                "event_id",
                context="transition fact",
            ),
            session_id=_required_payload_str(
                raw_fact,
                "session_id",
                context="transition fact",
            ),
            event_kind=_required_payload_str(
                raw_fact,
                "event_kind",
                context="transition fact",
            ),
            payload=cast(JSONObject, dict(fact_payload)),
            created_at=datetime.fromisoformat(
                _required_payload_str(
                    raw_fact,
                    "created_at",
                    context="transition fact",
                )
            ),
            session_seq=_required_payload_str(
                raw_fact,
                "session_seq",
                context="transition fact",
            ),
            projection_epoch=_required_payload_str(
                raw_fact,
                "projection_epoch",
                context="transition fact",
            ),
        )
        if fact.session_id != session_id:
            raise ValueError("stored transition fact belongs to another session")
        facts.append(fact)
    raw_cursor_payload = _required_mapping(
        payload,
        "raw_cursor",
        context="transition",
    )
    raw_cursor = RawCursor(
        session_id=_required_payload_str(
            raw_cursor_payload,
            "session_id",
            context="transition raw_cursor",
        ),
        session_seq=_required_payload_str(
            raw_cursor_payload,
            "session_seq",
            context="transition raw_cursor",
        ),
    )
    if raw_cursor.session_id != session_id:
        raise ValueError("stored transition cursor belongs to another session")
    notices = tuple(
        CommittedFactNotice(
            fact_id=fact.event_id,
            fact_kind=fact.event_kind,
            payload=fact.payload,
            session_seq=fact.session_seq,
            projection_epoch=(
                None
                if fact.projection_epoch is None
                else parse_u64(
                    fact.projection_epoch,
                    field_name="projection_epoch",
                )
            ),
        )
        for fact in facts
    )
    receipt = TransitionReceipt(
        session_id=session_id,
        projection_epoch=projection_epoch,
        transition_id=transition_id,
        mutation_fingerprint=mutation_fingerprint,
        state_version=state_version,
        facts=notices,
    )
    return state_version, tuple(facts), receipt, raw_cursor


@dataclass(frozen=True)
class RawCursor:
    session_id: str
    session_seq: str

    def __post_init__(self) -> None:
        _require_non_empty("session_id", self.session_id)
        parse_u64(self.session_seq, field_name="session_seq")


@dataclass(frozen=True)
class ProjectionCursor:
    kind: str
    session_id: str
    projection: str
    epoch: str
    session_seq: str

    def __post_init__(self) -> None:
        if self.kind not in {"delta", "settled"}:
            raise ValueError("kind must be 'delta' or 'settled'")
        _require_non_empty("session_id", self.session_id)
        _require_non_empty("projection", self.projection)
        parse_u64(self.epoch, field_name="epoch")
        parse_u64(self.session_seq, field_name="session_seq")


@dataclass(frozen=True)
class TrustedHandoff:
    session_id: str
    session_seq: str
    projection: str
    epoch: str
    payload: JSONObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty("session_id", self.session_id)
        parse_u64(self.session_seq, field_name="session_seq")
        _require_non_empty("projection", self.projection)
        parse_u64(self.epoch, field_name="epoch")
        _require_json_object("payload", self.payload)


@dataclass(frozen=True)
class SessionFactSourceState:
    session_id: str
    session_seq: str
    retention_floor: str
    projection: str
    projection_epoch: str
    trusted_handoff: TrustedHandoff | None = None
    dispatch_generation: str = "0"

    def __post_init__(self) -> None:
        _require_non_empty("session_id", self.session_id)
        parse_u64(self.session_seq, field_name="session_seq")
        parse_u64(self.retention_floor, field_name="retention_floor")
        _require_non_empty("projection", self.projection)
        parse_u64(self.projection_epoch, field_name="projection_epoch")
        parse_u64(self.dispatch_generation, field_name="dispatch_generation")
        if self.trusted_handoff is not None:
            if not isinstance(self.trusted_handoff, TrustedHandoff):
                raise TypeError("trusted_handoff must be a TrustedHandoff")
            if self.trusted_handoff.session_id != self.session_id:
                raise ValueError("trusted_handoff.session_id must match session_id")


@dataclass(frozen=True)
class RetentionFloorReplay:
    events: list[EventRecord]
    raw_cursor: RawCursor
    complete: bool

    def __post_init__(self) -> None:
        if not isinstance(self.events, list):
            raise TypeError("events must be a list")
        for event in self.events:
            if not isinstance(event, EventRecord):
                raise TypeError("events must contain EventRecord values")
        if not isinstance(self.raw_cursor, RawCursor):
            raise TypeError("raw_cursor must be a RawCursor")
        if not isinstance(self.complete, bool):
            raise TypeError("complete must be a bool")

    @classmethod
    def from_page(
        cls,
        *,
        session_id: str,
        events: list[EventRecord],
        limit: int,
        retention_floor: int,
        head_session_seq: str,
    ) -> RetentionFloorReplay:
        if events:
            last_seq = events[-1].session_seq
            if last_seq is None:
                raise ValueError("replayed event must include session_seq")
            return cls(
                events=events,
                raw_cursor=RawCursor(session_id=session_id, session_seq=last_seq),
                complete=len(events) < limit or last_seq == head_session_seq,
            )
        return cls(
            events=events,
            raw_cursor=RawCursor(
                session_id=session_id,
                session_seq=format_u64(max(retention_floor, 1) - 1),
            ),
            complete=True,
        )


@dataclass(frozen=True)
class AuthoritativeCommit:
    event: EventRecord | None
    projection: str
    projection_epoch: str
    raw_cursor: RawCursor
    idempotent: bool = False
    state_version: OperationStateVersion | None = None
    facts: tuple[EventRecord, ...] = ()
    transition_receipt: TransitionReceipt | None = None

    def __post_init__(self) -> None:
        _require_non_empty("projection", self.projection)
        commit_epoch = parse_u64(
            self.projection_epoch,
            field_name="projection_epoch",
        )
        facts = tuple(self.facts)
        if any(not isinstance(fact, EventRecord) for fact in facts):
            raise TypeError("facts must contain EventRecord values")
        object.__setattr__(self, "facts", facts)
        if self.event is not None:
            if self.event.session_seq is None:
                raise ValueError("committed event must include session_seq")
            if self.event.projection_epoch is None:
                raise ValueError("committed event must include projection_epoch")
            if self.raw_cursor.session_id != self.event.session_id:
                raise ValueError("raw cursor session_id must match the event")
            if self.raw_cursor.session_seq != self.event.session_seq:
                raise ValueError("raw cursor must land on the committed session_seq")
            if self.event.projection_epoch != self.projection_epoch:
                raise ValueError(
                    "committed event.projection_epoch must match commit.projection_epoch"
                )
            if (
                self.state_version is not None
                or facts
                or self.transition_receipt is not None
            ):
                raise ValueError(
                    "legacy commit cannot include transition result fields"
                )
            return
        if self.state_version is None or self.transition_receipt is None:
            raise ValueError("transition commit requires state version and receipt")
        if self.state_version.projection_epoch != commit_epoch:
            raise ValueError("state version projection_epoch must match commit")
        if self.transition_receipt.state_version != self.state_version:
            raise ValueError("transition receipt state version must match commit")
        if self.transition_receipt.session_id != self.raw_cursor.session_id:
            raise ValueError("transition receipt session_id must match raw cursor")
        if self.transition_receipt.projection_epoch != commit_epoch:
            raise ValueError("transition receipt projection_epoch must match commit")
        for fact in facts:
            if fact.session_id != self.raw_cursor.session_id:
                raise ValueError("committed fact belongs to another session")
            if fact.session_seq is None or fact.projection_epoch is None:
                raise ValueError(
                    "committed facts must include store sequence and epoch"
                )
            if fact.projection_epoch != self.projection_epoch:
                raise ValueError("committed fact projection_epoch must match commit")
        commit_ref = self.state_version.commit_ref
        if facts:
            if commit_ref.fact_seq_start != facts[0].session_seq:
                raise ValueError("commit_ref start must match first committed fact")
            if commit_ref.fact_seq_end != facts[-1].session_seq:
                raise ValueError("commit_ref end must match last committed fact")
            if self.raw_cursor.session_seq != facts[-1].session_seq:
                raise ValueError("raw cursor must land on the last committed fact")
        elif (
            commit_ref.fact_seq_start is not None or commit_ref.fact_seq_end is not None
        ):
            raise ValueError("fact-free transition must have empty commit_ref bounds")
