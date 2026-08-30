"""Runtime message and immutable transition primitives.

Each consumer owns its own cursor. Product approval stores should consume
``approval_decision`` messages with a cursor separate from the agentkit pipeline
cursor. ``approval_decision`` payloads should use ``{"request_id": str,
"approved": bool}`` plus product-specific routing fields. ``subagent_message``
payloads should use ``{"text": str}``.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol


def _require_non_empty(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be non-empty")


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("runtime contract mapping keys must be strings")
        return MappingProxyType(
            {key: _freeze_value(item) for key, item in value.items()}
        )
    if isinstance(value, tuple | list):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("runtime contract values must contain finite floats")
    if value is None or isinstance(value, bool | int | float | str):
        return value
    raise TypeError("runtime contract values must contain host-neutral JSON values")


def _freeze_mapping(value: Mapping[str, Any], *, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    if any(not isinstance(key, str) for key in value):
        raise TypeError(f"{field_name} keys must be strings")
    return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})


def _require_optional_sequence(value: str | None, *, field_name: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value.isdigit() or int(value) <= 0:
        raise ValueError(f"{field_name} must be a positive decimal string")


@dataclass(frozen=True, slots=True)
class CommitRef:
    """Store-stamped transition anchor and its contiguous committed fact range."""

    transition_id: str
    fact_seq_start: str | None = None
    fact_seq_end: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty("transition_id", self.transition_id)
        _require_optional_sequence(self.fact_seq_start, field_name="fact_seq_start")
        _require_optional_sequence(self.fact_seq_end, field_name="fact_seq_end")
        if (self.fact_seq_start is None) != (self.fact_seq_end is None):
            raise ValueError("fact sequence bounds must both be set or both be absent")
        if (
            self.fact_seq_start is not None
            and self.fact_seq_end is not None
            and int(self.fact_seq_start) > int(self.fact_seq_end)
        ):
            raise ValueError("fact_seq_start must not exceed fact_seq_end")


@dataclass(frozen=True, slots=True)
class OperationStateCAS:
    """Logical state compare-and-swap identity, independent of the physical log."""

    run_id: str
    revision: int
    projection_epoch: int

    def __post_init__(self) -> None:
        _require_non_empty("run_id", self.run_id)
        _require_non_negative_int("revision", self.revision)
        _require_non_negative_int("projection_epoch", self.projection_epoch)


@dataclass(frozen=True, slots=True)
class OperationStateVersion:
    """Immutable committed engine state stamped by the authoritative store."""

    run_id: str
    revision: int
    projection_epoch: int
    commit_ref: CommitRef
    value: Mapping[str, Any]

    def __post_init__(self) -> None:
        _require_non_empty("run_id", self.run_id)
        _require_non_negative_int("revision", self.revision)
        _require_non_negative_int("projection_epoch", self.projection_epoch)
        if not isinstance(self.commit_ref, CommitRef):
            raise TypeError("commit_ref must be a CommitRef")
        object.__setattr__(
            self,
            "value",
            _freeze_mapping(self.value, field_name="value"),
        )

    @property
    def cas(self) -> OperationStateCAS:
        return OperationStateCAS(
            run_id=self.run_id,
            revision=self.revision,
            projection_epoch=self.projection_epoch,
        )


@dataclass(frozen=True, slots=True)
class RuntimeCommand:
    command_id: str
    command_kind: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty("command_id", self.command_id)
        _require_non_empty("command_kind", self.command_kind)
        object.__setattr__(
            self,
            "payload",
            _freeze_mapping(self.payload, field_name="payload"),
        )


class CommandDispositionKind(StrEnum):
    APPLIED = "applied"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class AppliedCommandDisposition:
    command_id: str
    kind: CommandDispositionKind = field(
        default=CommandDispositionKind.APPLIED,
        init=False,
    )

    def __post_init__(self) -> None:
        _require_non_empty("command_id", self.command_id)


@dataclass(frozen=True, slots=True)
class RejectedCommandDisposition:
    command_id: str
    reason_code: str
    kind: CommandDispositionKind = field(
        default=CommandDispositionKind.REJECTED,
        init=False,
    )

    def __post_init__(self) -> None:
        _require_non_empty("command_id", self.command_id)
        _require_non_empty("reason_code", self.reason_code)


@dataclass(frozen=True, slots=True)
class SupersededCommandDisposition:
    command_id: str
    superseded_by_command_id: str
    kind: CommandDispositionKind = field(
        default=CommandDispositionKind.SUPERSEDED,
        init=False,
    )

    def __post_init__(self) -> None:
        _require_non_empty("command_id", self.command_id)
        _require_non_empty(
            "superseded_by_command_id",
            self.superseded_by_command_id,
        )
        if self.command_id == self.superseded_by_command_id:
            raise ValueError("a command cannot supersede itself")


CommandDisposition = (
    AppliedCommandDisposition
    | RejectedCommandDisposition
    | SupersededCommandDisposition
)


@dataclass(frozen=True, slots=True)
class PendingFact:
    fact_id: str
    fact_kind: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        _require_non_empty("fact_id", self.fact_id)
        _require_non_empty("fact_kind", self.fact_kind)
        object.__setattr__(
            self,
            "payload",
            _freeze_mapping(self.payload, field_name="payload"),
        )


@dataclass(frozen=True, slots=True)
class EffectPlan:
    effect_id: str
    attempt_id: str
    effect_kind: str
    payload: Mapping[str, Any]
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty("effect_id", self.effect_id)
        _require_non_empty("attempt_id", self.attempt_id)
        _require_non_empty("effect_kind", self.effect_kind)
        if self.idempotency_key is not None:
            _require_non_empty("idempotency_key", self.idempotency_key)
        object.__setattr__(
            self,
            "payload",
            _freeze_mapping(self.payload, field_name="payload"),
        )


class EffectStatus(StrEnum):
    PREPARED = "prepared"
    REJECTED = "rejected"
    DISPATCHED = "dispatched"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class ReconciliationOutcome(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ReconciliationRecord:
    effect_id: str
    attempt_id: str
    observed_outcome: ReconciliationOutcome
    evidence_ref: str
    actor_id: str
    owner_epoch: int
    transition_id: str

    def __post_init__(self) -> None:
        _require_non_empty("effect_id", self.effect_id)
        _require_non_empty("attempt_id", self.attempt_id)
        if not isinstance(self.observed_outcome, ReconciliationOutcome):
            object.__setattr__(
                self,
                "observed_outcome",
                ReconciliationOutcome(self.observed_outcome),
            )
        _require_non_empty("evidence_ref", self.evidence_ref)
        _require_non_empty("actor_id", self.actor_id)
        _require_non_negative_int("owner_epoch", self.owner_epoch)
        if self.owner_epoch == 0:
            raise ValueError("owner_epoch must be positive")
        _require_non_empty("transition_id", self.transition_id)


_LEGAL_EFFECT_TRANSITIONS = frozenset(
    {
        (EffectStatus.PREPARED, EffectStatus.REJECTED),
        (EffectStatus.PREPARED, EffectStatus.DISPATCHED),
        (EffectStatus.DISPATCHED, EffectStatus.COMPLETED),
        (EffectStatus.DISPATCHED, EffectStatus.FAILED),
        (EffectStatus.DISPATCHED, EffectStatus.UNKNOWN),
        (EffectStatus.UNKNOWN, EffectStatus.COMPLETED),
        (EffectStatus.UNKNOWN, EffectStatus.FAILED),
    }
)


@dataclass(frozen=True, slots=True)
class EffectMutation:
    effect_id: str
    attempt_id: str
    status: EffectStatus
    payload: Mapping[str, Any]
    expected_status: EffectStatus | None = None
    reconciliation: ReconciliationRecord | None = None

    def __post_init__(self) -> None:
        _require_non_empty("effect_id", self.effect_id)
        _require_non_empty("attempt_id", self.attempt_id)
        if not isinstance(self.status, EffectStatus):
            object.__setattr__(self, "status", EffectStatus(self.status))
        if self.expected_status is not None and not isinstance(
            self.expected_status,
            EffectStatus,
        ):
            object.__setattr__(
                self,
                "expected_status",
                EffectStatus(self.expected_status),
            )
        if self.expected_status is None:
            if self.status is not EffectStatus.PREPARED:
                raise ValueError("a new effect must enter the prepared state")
        elif (self.expected_status, self.status) not in _LEGAL_EFFECT_TRANSITIONS:
            raise ValueError(
                f"invalid effect transition: {self.expected_status.value}"
                f" -> {self.status.value}"
            )
        if self.expected_status is EffectStatus.UNKNOWN:
            if self.reconciliation is None:
                raise ValueError(
                    "unknown effect settlement requires a reconciliation record"
                )
            if self.reconciliation.effect_id != self.effect_id:
                raise ValueError("reconciliation effect_id must match effect mutation")
            if self.reconciliation.attempt_id != self.attempt_id:
                raise ValueError("reconciliation attempt_id must match effect mutation")
            if self.reconciliation.observed_outcome.value != self.status.value:
                raise ValueError(
                    "reconciliation outcome must match effect mutation status"
                )
        elif self.reconciliation is not None:
            raise ValueError(
                "reconciliation is valid only for an unknown effect settlement"
            )
        object.__setattr__(
            self,
            "payload",
            _freeze_mapping(self.payload, field_name="payload"),
        )

    @classmethod
    def prepare(cls, plan: EffectPlan) -> EffectMutation:
        if not isinstance(plan, EffectPlan):
            raise TypeError("plan must be an EffectPlan")
        return cls(
            effect_id=plan.effect_id,
            attempt_id=plan.attempt_id,
            status=EffectStatus.PREPARED,
            payload={
                "effect_kind": plan.effect_kind,
                "payload": plan.payload,
                "idempotency_key": plan.idempotency_key,
            },
        )


@dataclass(frozen=True, slots=True)
class TransitionProposal:
    transition_id: str
    state_value: Mapping[str, Any]
    pending_facts: tuple[PendingFact, ...] = ()
    dispositions: tuple[CommandDisposition, ...] = ()
    effect_mutation: EffectMutation | None = None

    def __post_init__(self) -> None:
        _require_non_empty("transition_id", self.transition_id)
        object.__setattr__(
            self,
            "state_value",
            _freeze_mapping(self.state_value, field_name="state_value"),
        )
        facts = tuple(self.pending_facts)
        if any(not isinstance(fact, PendingFact) for fact in facts):
            raise TypeError("pending_facts must contain PendingFact values")
        object.__setattr__(self, "pending_facts", facts)
        dispositions = tuple(self.dispositions)
        allowed = (
            AppliedCommandDisposition,
            RejectedCommandDisposition,
            SupersededCommandDisposition,
        )
        if any(not isinstance(disposition, allowed) for disposition in dispositions):
            raise TypeError("dispositions must contain typed command dispositions")
        command_ids = [disposition.command_id for disposition in dispositions]
        if len(command_ids) != len(set(command_ids)):
            raise ValueError("a proposal may disposition each command at most once")
        object.__setattr__(self, "dispositions", dispositions)
        if self.effect_mutation is not None and not isinstance(
            self.effect_mutation,
            EffectMutation,
        ):
            raise TypeError("effect_mutation must be an EffectMutation")


class StreamFrameKind(StrEnum):
    TOKEN_DELTA = "token_delta"
    THINKING_DELTA = "thinking_delta"
    HEARTBEAT = "heartbeat"


@dataclass(frozen=True, slots=True)
class StreamFrame:
    frame_id: str
    kind: StreamFrameKind
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        _require_non_empty("frame_id", self.frame_id)
        if not isinstance(self.kind, StreamFrameKind):
            object.__setattr__(self, "kind", StreamFrameKind(self.kind))
        object.__setattr__(
            self,
            "payload",
            _freeze_mapping(self.payload, field_name="payload"),
        )


@dataclass(frozen=True, slots=True)
class CommittedFactNotice:
    fact_id: str
    fact_kind: str
    payload: Mapping[str, Any]
    session_seq: str | None = None
    projection_epoch: int | None = None

    def __post_init__(self) -> None:
        _require_non_empty("fact_id", self.fact_id)
        _require_non_empty("fact_kind", self.fact_kind)
        _require_optional_sequence(self.session_seq, field_name="session_seq")
        if self.projection_epoch is not None:
            _require_non_negative_int("projection_epoch", self.projection_epoch)
        object.__setattr__(
            self,
            "payload",
            _freeze_mapping(self.payload, field_name="payload"),
        )


@dataclass(frozen=True, slots=True)
class TransitionReceipt:
    session_id: str
    projection_epoch: int
    transition_id: str
    mutation_fingerprint: str
    state_version: OperationStateVersion
    facts: tuple[CommittedFactNotice, ...]

    def __post_init__(self) -> None:
        _require_non_empty("session_id", self.session_id)
        _require_non_negative_int("projection_epoch", self.projection_epoch)
        _require_non_empty("transition_id", self.transition_id)
        _require_non_empty("mutation_fingerprint", self.mutation_fingerprint)
        if not isinstance(self.state_version, OperationStateVersion):
            raise TypeError("state_version must be an OperationStateVersion")
        if self.state_version.projection_epoch != self.projection_epoch:
            raise ValueError("state_version projection_epoch must match receipt")
        if self.state_version.commit_ref.transition_id != self.transition_id:
            raise ValueError("state_version transition_id must match receipt")
        facts = tuple(self.facts)
        if any(not isinstance(fact, CommittedFactNotice) for fact in facts):
            raise TypeError("facts must contain CommittedFactNotice values")
        if any(
            fact.projection_epoch is not None
            and fact.projection_epoch != self.projection_epoch
            for fact in facts
        ):
            raise ValueError("fact projection_epoch must match receipt")
        object.__setattr__(self, "facts", facts)


class RuntimeMessageKind(StrEnum):
    """Inbound runtime controls consumed by pipeline safe points."""

    INTERRUPT = "interrupt"
    USER_STEER = "user_steer"
    APPROVAL_DECISION = "approval_decision"
    SUBAGENT_MESSAGE = "subagent_message"
    SYSTEM_NOTICE = "system_notice"


class DuplicateRuntimeMessageError(ValueError):
    """Raised when a runtime bus rejects an already-published message ID."""

    def __init__(self, message_id: str) -> None:
        super().__init__(f"duplicate runtime message_id: {message_id}")
        self.message_id = message_id


def _coerce_runtime_message_kind(kind: RuntimeMessageKind | str) -> RuntimeMessageKind:
    try:
        return RuntimeMessageKind(kind)
    except ValueError as exc:
        raise ValueError(f"unknown runtime message kind: {kind}") from exc


def _normalize_runtime_message_kinds(
    kinds: Iterable[RuntimeMessageKind | str] | RuntimeMessageKind | str | None,
) -> frozenset[RuntimeMessageKind] | None:
    if kinds is None:
        return None
    if isinstance(kinds, str):
        kinds = (kinds,)

    normalized = frozenset(_coerce_runtime_message_kind(kind) for kind in kinds)
    if not normalized:
        raise ValueError("kinds must be non-empty when provided")
    return normalized


@dataclass(frozen=True, slots=True)
class RuntimeMessage:
    """A runtime control message before it is assigned a bus sequence."""

    message_id: str
    kind: RuntimeMessageKind
    payload: Mapping[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not self.message_id:
            raise ValueError("message_id must be non-empty")
        if not isinstance(self.kind, RuntimeMessageKind):
            object.__setattr__(
                self,
                "kind",
                _coerce_runtime_message_kind(self.kind),
            )
        if isinstance(self.created_at, bool) or not isinstance(
            self.created_at, int | float
        ):
            raise TypeError("created_at must be a number")
        if isinstance(self.created_at, float) and not math.isfinite(self.created_at):
            raise ValueError("created_at must be finite")
        if self.created_at < 0:
            raise ValueError("created_at must be non-negative")
        object.__setattr__(
            self,
            "payload",
            _freeze_mapping(self.payload, field_name="payload"),
        )


@dataclass(frozen=True, slots=True)
class RuntimeMessageCursor:
    """Idempotent cursor for consuming messages after a sequence number."""

    sequence: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise TypeError("sequence must be an integer")
        if self.sequence < 0:
            raise ValueError("sequence must be non-negative")


@dataclass(frozen=True, slots=True)
class SequencedRuntimeMessage:
    """Runtime message after durable sequencing by a bus."""

    sequence: int
    message: RuntimeMessage

    def __post_init__(self) -> None:
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise TypeError("sequence must be an integer")
        if self.sequence <= 0:
            raise ValueError("sequence must be positive")


@dataclass(frozen=True, slots=True)
class RuntimeMessageBatch:
    """Messages returned for a cursor plus the next cursor to persist."""

    messages: tuple[SequencedRuntimeMessage, ...]
    cursor: RuntimeMessageCursor


class RuntimeMessageBus(Protocol):
    """Inbound message bus with caller-owned idempotent cursor advancement."""

    async def publish(self, message: RuntimeMessage) -> SequencedRuntimeMessage: ...

    async def consume_after(
        self,
        cursor: RuntimeMessageCursor,
        *,
        kinds: Iterable[RuntimeMessageKind | str] | None = None,
        limit: int | None = None,
    ) -> RuntimeMessageBatch: ...


class InMemoryRuntimeMessageBus:
    """Process-local runtime message bus for tests and single-runtime wiring.

    The bus keeps all messages and message IDs in memory, so it is not suitable
    for long-running durable processes.
    """

    def __init__(self) -> None:
        self._messages: list[SequencedRuntimeMessage] = []
        self._message_ids: set[str] = set()
        self._lock = asyncio.Lock()

    async def publish(self, message: RuntimeMessage) -> SequencedRuntimeMessage:
        async with self._lock:
            if message.message_id in self._message_ids:
                raise DuplicateRuntimeMessageError(message.message_id)
            sequenced = SequencedRuntimeMessage(
                sequence=len(self._messages) + 1,
                message=message,
            )
            self._messages.append(sequenced)
            self._message_ids.add(message.message_id)
            return sequenced

    async def consume_after(
        self,
        cursor: RuntimeMessageCursor,
        *,
        kinds: Iterable[RuntimeMessageKind | str] | None = None,
        limit: int | None = None,
    ) -> RuntimeMessageBatch:
        normalized_kinds = _normalize_runtime_message_kinds(kinds)
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise TypeError("limit must be an integer or None")
            if limit <= 0:
                raise ValueError("limit must be positive")

        async with self._lock:
            messages = [
                item for item in self._messages if item.sequence > cursor.sequence
            ]
            if normalized_kinds is not None:
                messages = [
                    item for item in messages if item.message.kind in normalized_kinds
                ]
            if limit is not None:
                messages = messages[:limit]
            next_cursor = (
                RuntimeMessageCursor(messages[-1].sequence) if messages else cursor
            )
            return RuntimeMessageBatch(
                messages=tuple(messages),
                cursor=next_cursor,
            )
