"""Frozen host-neutral contracts for the AgentKit engine and coordinator."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol, TypeAlias


def _require_non_empty(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be non-empty")


def _require_non_blank(field_name: str, value: str) -> None:
    _require_non_empty(field_name, value)
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")


def _require_positive_int(field_name: str, value: int) -> None:
    _require_non_negative_int(field_name, value)
    if value == 0:
        raise ValueError(f"{field_name} must be positive")


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
    """Store-stamped transition anchor and contiguous committed fact range."""

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
    """Logical state CAS identity, independent of the physical event log."""

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
            self, "value", _freeze_mapping(self.value, field_name="value")
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
        _require_non_empty("superseded_by_command_id", self.superseded_by_command_id)
        if self.command_id == self.superseded_by_command_id:
            raise ValueError("a command cannot supersede itself")


CommandDisposition: TypeAlias = (
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
    requires_approval: bool = False
    approval_request_id: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty("effect_id", self.effect_id)
        _require_non_empty("attempt_id", self.attempt_id)
        _require_non_empty("effect_kind", self.effect_kind)
        if self.idempotency_key is not None:
            _require_non_empty("idempotency_key", self.idempotency_key)
        if not isinstance(self.requires_approval, bool):
            raise TypeError("requires_approval must be a bool")
        if self.requires_approval:
            if self.approval_request_id is None:
                raise ValueError(
                    "approval_request_id is required when requires_approval is true"
                )
            _require_non_empty("approval_request_id", self.approval_request_id)
        elif self.approval_request_id is not None:
            raise ValueError(
                "approval_request_id is valid only when requires_approval is true"
            )
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
        _require_positive_int("owner_epoch", self.owner_epoch)
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
    """Phase B store-UoW mutation retained until the Phase D cutover."""

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
        payload = _freeze_mapping(self.payload, field_name="payload")
        forbidden = {"tool_calls", "usage"}
        if forbidden.intersection(payload):
            raise ValueError("StreamFrame cannot carry tool calls or usage")
        object.__setattr__(self, "payload", payload)


@dataclass(frozen=True, slots=True)
class CommittedFactNotice:
    fact_id: str
    fact_kind: str
    payload: Mapping[str, Any]
    session_seq: str | None = None
    projection_epoch: int | None = None
    event_record_id: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty("fact_id", self.fact_id)
        _require_non_empty("fact_kind", self.fact_kind)
        _require_optional_sequence(self.session_seq, field_name="session_seq")
        if self.projection_epoch is not None:
            _require_non_negative_int("projection_epoch", self.projection_epoch)
        if self.event_record_id is not None:
            _require_non_empty("event_record_id", self.event_record_id)
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
    effect_plans: tuple[EffectPlan, ...] = ()

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
        plans = tuple(self.effect_plans)
        if any(not isinstance(plan, EffectPlan) for plan in plans):
            raise TypeError("effect_plans must contain EffectPlan values")
        effect_ids = [plan.effect_id for plan in plans]
        if len(effect_ids) != len(set(effect_ids)):
            raise ValueError("effect_plans must contain unique effect identities")
        object.__setattr__(self, "facts", facts)
        object.__setattr__(self, "effect_plans", plans)


@dataclass(frozen=True, slots=True)
class ModelToolCall:
    tool_call_id: str
    name: str
    arguments: Mapping[str, Any]
    idempotency_key: str | None = None
    requires_approval: bool = False
    approval_request_id: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty("tool_call_id", self.tool_call_id)
        _require_non_empty("name", self.name)
        if self.idempotency_key is not None:
            _require_non_empty("idempotency_key", self.idempotency_key)
        if not isinstance(self.requires_approval, bool):
            raise TypeError("requires_approval must be a bool")
        if self.requires_approval:
            if self.approval_request_id is None:
                raise ValueError(
                    "approval_request_id is required when requires_approval is true"
                )
            _require_non_empty("approval_request_id", self.approval_request_id)
        elif self.approval_request_id is not None:
            raise ValueError(
                "approval_request_id is valid only when requires_approval is true"
            )
        object.__setattr__(
            self,
            "arguments",
            _freeze_mapping(self.arguments, field_name="arguments"),
        )


@dataclass(frozen=True, slots=True)
class ModelUsage:
    input_tokens: int
    output_tokens: int
    provider_details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_negative_int("input_tokens", self.input_tokens)
        _require_non_negative_int("output_tokens", self.output_tokens)
        object.__setattr__(
            self,
            "provider_details",
            _freeze_mapping(self.provider_details, field_name="provider_details"),
        )

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class ProviderStopMetadata:
    reason: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty("reason", self.reason)
        object.__setattr__(
            self,
            "details",
            _freeze_mapping(self.details, field_name="details"),
        )


@dataclass(frozen=True, slots=True)
class ModelGenerationResult:
    result_id: str
    request_id: str
    assistant_content: str
    finalized_thinking: str | None
    tool_calls: tuple[ModelToolCall, ...]
    usage: ModelUsage
    provider_stop: ProviderStopMetadata

    def __post_init__(self) -> None:
        _require_non_empty("result_id", self.result_id)
        _require_non_empty("request_id", self.request_id)
        if not isinstance(self.assistant_content, str):
            raise TypeError("assistant_content must be a string")
        if self.finalized_thinking is not None and not isinstance(
            self.finalized_thinking,
            str,
        ):
            raise TypeError("finalized_thinking must be a string or None")
        calls = tuple(self.tool_calls)
        if any(not isinstance(call, ModelToolCall) for call in calls):
            raise TypeError("tool_calls must contain ModelToolCall values")
        call_ids = [call.tool_call_id for call in calls]
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("tool_calls must contain unique tool_call_id values")
        if not isinstance(self.usage, ModelUsage):
            raise TypeError("usage must be a ModelUsage")
        if not isinstance(self.provider_stop, ProviderStopMetadata):
            raise TypeError("provider_stop must be ProviderStopMetadata")
        object.__setattr__(self, "tool_calls", calls)


@dataclass(frozen=True, slots=True)
class ModelRequest:
    request_id: str
    run_id: str
    round_index: int
    commands: tuple[RuntimeCommand, ...]
    context: Mapping[str, Any]

    def __post_init__(self) -> None:
        _require_non_empty("request_id", self.request_id)
        _require_non_empty("run_id", self.run_id)
        _require_positive_int("round_index", self.round_index)
        commands = tuple(self.commands)
        if any(not isinstance(command, RuntimeCommand) for command in commands):
            raise TypeError("commands must contain RuntimeCommand values")
        command_ids = [command.command_id for command in commands]
        if len(command_ids) != len(set(command_ids)):
            raise ValueError("commands must contain unique command identities")
        object.__setattr__(self, "commands", commands)
        object.__setattr__(
            self,
            "context",
            _freeze_mapping(self.context, field_name="context"),
        )


class EffectSettlementOutcome(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True, slots=True)
class EffectSettlement:
    input_id: str
    tool_call_id: str
    tool_name: str
    effect_id: str
    attempt_id: str
    authorization_transition_id: str
    owner_epoch: int
    outcome: EffectSettlementOutcome
    result: Mapping[str, Any] = field(default_factory=dict)
    reason_code: str | None = None
    reason_message: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "input_id",
            "tool_call_id",
            "tool_name",
            "effect_id",
            "attempt_id",
            "authorization_transition_id",
        ):
            _require_non_empty(field_name, getattr(self, field_name))
        _require_positive_int("owner_epoch", self.owner_epoch)
        if not isinstance(self.outcome, EffectSettlementOutcome):
            object.__setattr__(
                self,
                "outcome",
                EffectSettlementOutcome(self.outcome),
            )
        if self.outcome is EffectSettlementOutcome.COMPLETED:
            if self.reason_code is not None or self.reason_message is not None:
                raise ValueError(
                    "completed effect settlement cannot carry a failure reason"
                )
        else:
            if self.reason_code is None or self.reason_message is None:
                raise ValueError(
                    "failed and indeterminate settlements require a stable reason"
                )
            _require_non_empty("reason_code", self.reason_code)
            _require_non_blank("reason_message", self.reason_message)
        object.__setattr__(
            self,
            "result",
            _freeze_mapping(self.result, field_name="result"),
        )

    @classmethod
    def completed(
        cls,
        *,
        input_id: str,
        tool_call_id: str,
        tool_name: str,
        effect_id: str,
        attempt_id: str,
        authorization_transition_id: str,
        owner_epoch: int,
        result: Mapping[str, Any],
    ) -> EffectSettlement:
        return cls(
            input_id=input_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            effect_id=effect_id,
            attempt_id=attempt_id,
            authorization_transition_id=authorization_transition_id,
            owner_epoch=owner_epoch,
            outcome=EffectSettlementOutcome.COMPLETED,
            result=result,
        )


@dataclass(frozen=True, slots=True)
class ApprovalSettlement:
    input_id: str
    command_id: str
    tool_call_id: str
    tool_name: str
    effect_id: str
    attempt_id: str
    transition_id: str
    owner_epoch: int
    approved: bool
    rejection_reason_code: str | None = None
    rejection_message: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "input_id",
            "command_id",
            "tool_call_id",
            "tool_name",
            "effect_id",
            "attempt_id",
            "transition_id",
        ):
            _require_non_empty(field_name, getattr(self, field_name))
        _require_positive_int("owner_epoch", self.owner_epoch)
        if not isinstance(self.approved, bool):
            raise TypeError("approved must be a bool")
        if self.approved:
            if (
                self.rejection_reason_code is not None
                or self.rejection_message is not None
            ):
                raise ValueError("approved settlement cannot carry a rejection reason")
        else:
            if self.rejection_reason_code is None or self.rejection_message is None:
                raise ValueError("approval denial requires a stable rejection reason")
            _require_non_empty("rejection_reason_code", self.rejection_reason_code)
            _require_non_blank("rejection_message", self.rejection_message)


@dataclass(frozen=True, slots=True)
class Initial:
    input_id: str
    command_batch: tuple[RuntimeCommand, ...]
    mailbox_cut: int

    def __post_init__(self) -> None:
        _require_non_empty("input_id", self.input_id)
        commands = tuple(self.command_batch)
        if any(not isinstance(command, RuntimeCommand) for command in commands):
            raise TypeError("command_batch must contain RuntimeCommand values")
        command_ids = [command.command_id for command in commands]
        if len(command_ids) != len(set(command_ids)):
            raise ValueError("command_batch must contain unique command identities")
        _require_non_negative_int("mailbox_cut", self.mailbox_cut)
        object.__setattr__(self, "command_batch", commands)


@dataclass(frozen=True, slots=True)
class ModelGenerationCompleted:
    result: ModelGenerationResult

    def __post_init__(self) -> None:
        if not isinstance(self.result, ModelGenerationResult):
            raise TypeError("result must be a ModelGenerationResult")

    @property
    def input_id(self) -> str:
        return self.result.result_id


@dataclass(frozen=True, slots=True)
class EffectSettled:
    settlement: EffectSettlement

    def __post_init__(self) -> None:
        if not isinstance(self.settlement, EffectSettlement):
            raise TypeError("settlement must be an EffectSettlement")

    @property
    def input_id(self) -> str:
        return self.settlement.input_id


@dataclass(frozen=True, slots=True)
class ApprovalResolved:
    settlement: ApprovalSettlement

    def __post_init__(self) -> None:
        if not isinstance(self.settlement, ApprovalSettlement):
            raise TypeError("settlement must be an ApprovalSettlement")

    @property
    def input_id(self) -> str:
        return self.settlement.input_id


EngineStepInput: TypeAlias = (
    Initial | ModelGenerationCompleted | EffectSettled | ApprovalResolved
)


def _identity_value(
    mapping: Mapping[str, Any],
    key: str,
    *,
    subject: str,
) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{subject} {key} must be non-empty")
    return value


def _pending_effect_identity(
    state_version: OperationStateVersion,
) -> tuple[str, str, str, str]:
    runtime_state = state_version.value.get("_agentkit_runtime")
    if not isinstance(runtime_state, Mapping):
        raise ValueError("settlement requires engine runtime state")
    pending_plans = runtime_state.get("pending_effect_plans")
    if not isinstance(pending_plans, tuple | list) or not pending_plans:
        raise ValueError("settlement requires a pending effect plan")
    raw_plan = pending_plans[0]
    if not isinstance(raw_plan, Mapping):
        raise ValueError("pending effect plan must be a mapping")
    payload = raw_plan.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError("pending effect plan payload must be a mapping")
    return (
        _identity_value(raw_plan, "effect_id", subject="pending effect plan"),
        _identity_value(raw_plan, "attempt_id", subject="pending effect plan"),
        _identity_value(payload, "tool_call_id", subject="pending effect plan"),
        _identity_value(payload, "tool_name", subject="pending effect plan"),
    )


def _effect_plan_identity(plan: EffectPlan) -> tuple[str, str, str, str]:
    tool_call_id = plan.payload.get("tool_call_id")
    tool_name = plan.payload.get("tool_name")
    if not isinstance(tool_call_id, str) or not tool_call_id:
        raise ValueError("effect plan tool_call_id must be non-empty")
    if not isinstance(tool_name, str) or not tool_name:
        raise ValueError("effect plan tool_name must be non-empty")
    return plan.effect_id, plan.attempt_id, tool_call_id, tool_name


def _validate_effect_plan_binding(
    state_version: OperationStateVersion,
    plan: EffectPlan,
) -> None:
    expected = _pending_effect_identity(state_version)
    actual = _effect_plan_identity(plan)
    field_names = ("effect_id", "attempt_id", "tool_call_id", "tool_name")
    for field_name, actual_value, expected_value in zip(
        field_names,
        actual,
        expected,
        strict=True,
    ):
        if actual_value != expected_value:
            raise ValueError(f"effect plan {field_name} must match pending effect plan")


def _validate_settlement_binding(
    state_version: OperationStateVersion,
    settlement: EffectSettlement | ApprovalSettlement,
    *,
    owner_epoch: int,
) -> None:
    if settlement.owner_epoch != owner_epoch:
        raise ValueError("settlement owner_epoch must match request owner_epoch")
    if isinstance(settlement, EffectSettlement):
        transition_field = "authorization_transition_id"
        transition_id = settlement.authorization_transition_id
    else:
        transition_field = "transition_id"
        transition_id = settlement.transition_id
    if transition_id != state_version.commit_ref.transition_id:
        raise ValueError(
            f"settlement {transition_field} must match owning committed transition"
        )
    expected = _pending_effect_identity(state_version)
    actual = (
        settlement.effect_id,
        settlement.attempt_id,
        settlement.tool_call_id,
        settlement.tool_name,
    )
    field_names = ("effect_id", "attempt_id", "tool_call_id", "tool_name")
    for field_name, actual_value, expected_value in zip(
        field_names,
        actual,
        expected,
        strict=True,
    ):
        if actual_value != expected_value:
            raise ValueError(f"settlement {field_name} must match pending effect plan")


@dataclass(frozen=True, slots=True)
class EngineStepRequest:
    state_version: OperationStateVersion
    step_input: EngineStepInput

    def __post_init__(self) -> None:
        if not isinstance(self.state_version, OperationStateVersion):
            raise TypeError("state_version must be an OperationStateVersion")
        if not isinstance(
            self.step_input,
            (Initial, ModelGenerationCompleted, EffectSettled, ApprovalResolved),
        ):
            raise TypeError("step_input must be an EngineStepInput variant")


class PreparedEffectActionKind(StrEnum):
    DISPATCH = "dispatch"
    APPROVAL_WAIT = "approval_wait"


@dataclass(frozen=True, slots=True)
class EffectReference:
    effect_id: str
    attempt_id: str
    tool_call_id: str
    approval_request_id: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty("effect_id", self.effect_id)
        _require_non_empty("attempt_id", self.attempt_id)
        _require_non_empty("tool_call_id", self.tool_call_id)
        if self.approval_request_id is not None:
            _require_non_empty("approval_request_id", self.approval_request_id)

    @classmethod
    def from_plan(cls, plan: EffectPlan) -> EffectReference:
        tool_call_id = plan.payload.get("tool_call_id")
        if not isinstance(tool_call_id, str) or not tool_call_id:
            raise ValueError("effect plan payload requires a non-empty tool_call_id")
        return cls(
            effect_id=plan.effect_id,
            attempt_id=plan.attempt_id,
            tool_call_id=tool_call_id,
            approval_request_id=plan.approval_request_id,
        )


@dataclass(frozen=True, slots=True)
class ModelGenerationAction:
    request: ModelRequest

    def __post_init__(self) -> None:
        if not isinstance(self.request, ModelRequest):
            raise TypeError("request must be a ModelRequest")


@dataclass(frozen=True, slots=True)
class PreparedEffectAction:
    effect_plan: EffectPlan
    action_kind: PreparedEffectActionKind

    def __post_init__(self) -> None:
        if not isinstance(self.effect_plan, EffectPlan):
            raise TypeError("effect_plan must be an EffectPlan")
        if not isinstance(self.action_kind, PreparedEffectActionKind):
            object.__setattr__(
                self,
                "action_kind",
                PreparedEffectActionKind(self.action_kind),
            )
        if (
            self.action_kind is PreparedEffectActionKind.APPROVAL_WAIT
            and not self.effect_plan.requires_approval
        ):
            raise ValueError("approval wait effect plan requires_approval")


@dataclass(frozen=True, slots=True)
class TerminalAction:
    final_message: str | None
    stop_reason: str

    def __post_init__(self) -> None:
        if self.final_message is not None and not isinstance(self.final_message, str):
            raise TypeError("final_message must be a string or None")
        _require_non_empty("stop_reason", self.stop_reason)


@dataclass(frozen=True, slots=True)
class BlockedAction:
    reason: str
    effect: EffectReference | None = None

    def __post_init__(self) -> None:
        _require_non_empty("reason", self.reason)
        if self.effect is not None and not isinstance(self.effect, EffectReference):
            raise TypeError("effect must be an EffectReference or None")


@dataclass(frozen=True, slots=True)
class SafeYieldAction:
    reason: str

    def __post_init__(self) -> None:
        _require_non_empty("reason", self.reason)


NextAction: TypeAlias = (
    ModelGenerationAction
    | PreparedEffectAction
    | TerminalAction
    | BlockedAction
    | SafeYieldAction
)


@dataclass(frozen=True, slots=True)
class TransitionProposal:
    transition_id: str
    state_value: Mapping[str, Any]
    next_action: NextAction
    pending_facts: tuple[PendingFact, ...] = ()
    dispositions: tuple[CommandDisposition, ...] = ()
    effect_plans: tuple[EffectPlan, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty("transition_id", self.transition_id)
        object.__setattr__(
            self,
            "state_value",
            _freeze_mapping(self.state_value, field_name="state_value"),
        )
        if not isinstance(
            self.next_action,
            (
                ModelGenerationAction,
                PreparedEffectAction,
                TerminalAction,
                BlockedAction,
                SafeYieldAction,
            ),
        ):
            raise TypeError("next_action must be a NextAction variant")
        facts = tuple(self.pending_facts)
        if any(not isinstance(fact, PendingFact) for fact in facts):
            raise TypeError("pending_facts must contain PendingFact values")
        dispositions = tuple(self.dispositions)
        allowed_dispositions = (
            AppliedCommandDisposition,
            RejectedCommandDisposition,
            SupersededCommandDisposition,
        )
        if any(
            not isinstance(disposition, allowed_dispositions)
            for disposition in dispositions
        ):
            raise TypeError("dispositions must contain typed command dispositions")
        command_ids = [disposition.command_id for disposition in dispositions]
        if len(command_ids) != len(set(command_ids)):
            raise ValueError("a proposal may disposition each command at most once")
        plans = tuple(self.effect_plans)
        if any(not isinstance(plan, EffectPlan) for plan in plans):
            raise TypeError("effect_plans must contain EffectPlan values")
        effect_ids = [plan.effect_id for plan in plans]
        if len(effect_ids) != len(set(effect_ids)):
            raise ValueError("effect_plans must contain unique effect identities")
        if isinstance(self.next_action, PreparedEffectAction):
            planned = {plan.effect_id for plan in plans}
            if planned and self.next_action.effect_plan.effect_id not in planned:
                raise ValueError(
                    "prepared next action must refer to an ordered effect plan"
                )
        object.__setattr__(self, "pending_facts", facts)
        object.__setattr__(self, "dispositions", dispositions)
        object.__setattr__(self, "effect_plans", plans)


@dataclass(frozen=True, slots=True)
class RunSegmentRequest:
    session_id: str
    owner_id: str
    owner_epoch: int
    state_version: OperationStateVersion
    step_input: EngineStepInput
    max_rounds: int

    def __post_init__(self) -> None:
        _require_non_empty("session_id", self.session_id)
        _require_non_empty("owner_id", self.owner_id)
        _require_positive_int("owner_epoch", self.owner_epoch)
        if not isinstance(self.state_version, OperationStateVersion):
            raise TypeError("state_version must be an OperationStateVersion")
        if not isinstance(
            self.step_input,
            (Initial, ModelGenerationCompleted, EffectSettled, ApprovalResolved),
        ):
            raise TypeError("step_input must be an EngineStepInput variant")
        if isinstance(self.step_input, EffectSettled):
            _validate_settlement_binding(
                self.state_version,
                self.step_input.settlement,
                owner_epoch=self.owner_epoch,
            )
        elif isinstance(self.step_input, ApprovalResolved):
            _validate_settlement_binding(
                self.state_version,
                self.step_input.settlement,
                owner_epoch=self.owner_epoch,
            )
        _require_positive_int("max_rounds", self.max_rounds)


@dataclass(frozen=True, slots=True)
class CommitTransitionRequest:
    session_id: str
    owner_id: str
    owner_epoch: int
    engine_request: EngineStepRequest
    proposal: TransitionProposal

    def __post_init__(self) -> None:
        _validate_commit_request(
            session_id=self.session_id,
            owner_id=self.owner_id,
            owner_epoch=self.owner_epoch,
            engine_request=self.engine_request,
            proposal=self.proposal,
        )


Settlement: TypeAlias = EffectSettlement | ApprovalSettlement


@dataclass(frozen=True, slots=True)
class CommitSettlementRequest:
    session_id: str
    owner_id: str
    owner_epoch: int
    engine_request: EngineStepRequest
    proposal: TransitionProposal
    settlement: Settlement
    effect_mutation: EffectMutation

    def __post_init__(self) -> None:
        _validate_commit_request(
            session_id=self.session_id,
            owner_id=self.owner_id,
            owner_epoch=self.owner_epoch,
            engine_request=self.engine_request,
            proposal=self.proposal,
        )
        if not isinstance(self.settlement, (EffectSettlement, ApprovalSettlement)):
            raise TypeError("settlement must be a settlement variant")
        _validate_settlement_binding(
            self.engine_request.state_version,
            self.settlement,
            owner_epoch=self.owner_epoch,
        )
        if not isinstance(self.effect_mutation, EffectMutation):
            raise TypeError("effect_mutation must be an EffectMutation")
        if self.effect_mutation.effect_id != self.settlement.effect_id:
            raise ValueError("effect mutation and settlement effect_id must match")
        if self.effect_mutation.attempt_id != self.settlement.attempt_id:
            raise ValueError("effect mutation and settlement attempt_id must match")


@dataclass(frozen=True, slots=True)
class DispatchAuthorizationRequest:
    session_id: str
    owner_id: str
    owner_epoch: int
    mailbox_cut: int
    engine_request: EngineStepRequest
    proposal: TransitionProposal
    effect_plan: EffectPlan
    effect_mutation: EffectMutation
    approval_settlement: ApprovalSettlement | None = None

    def __post_init__(self) -> None:
        _validate_commit_request(
            session_id=self.session_id,
            owner_id=self.owner_id,
            owner_epoch=self.owner_epoch,
            engine_request=self.engine_request,
            proposal=self.proposal,
        )
        _require_non_negative_int("mailbox_cut", self.mailbox_cut)
        if not isinstance(self.effect_plan, EffectPlan):
            raise TypeError("effect_plan must be an EffectPlan")
        _validate_effect_plan_binding(
            self.engine_request.state_version,
            self.effect_plan,
        )
        if not isinstance(self.proposal.next_action, PreparedEffectAction):
            raise ValueError(
                "dispatch authorization proposal must prepare an effect dispatch"
            )
        if (
            self.proposal.next_action.action_kind
            is not PreparedEffectActionKind.DISPATCH
        ):
            raise ValueError(
                "dispatch authorization proposal must use the dispatch action"
            )
        if _effect_plan_identity(self.proposal.next_action.effect_plan) != (
            _effect_plan_identity(self.effect_plan)
        ):
            raise ValueError(
                "dispatch authorization plan must match proposal next action"
            )
        if not isinstance(self.effect_mutation, EffectMutation):
            raise TypeError("effect_mutation must be an EffectMutation")
        if self.effect_mutation.effect_id != self.effect_plan.effect_id:
            raise ValueError("effect mutation and plan effect_id must match")
        if self.effect_mutation.attempt_id != self.effect_plan.attempt_id:
            raise ValueError("effect mutation and plan attempt_id must match")
        if (
            self.effect_mutation.expected_status is not EffectStatus.PREPARED
            or self.effect_mutation.status is not EffectStatus.DISPATCHED
        ):
            raise ValueError(
                "dispatch authorization requires prepared -> dispatched mutation"
            )
        if self.effect_plan.requires_approval and self.approval_settlement is None:
            raise ValueError("approved settlement is required before dispatch")
        if (
            not self.effect_plan.requires_approval
            and self.approval_settlement is not None
        ):
            raise ValueError(
                "approval settlement is valid only for an approval-gated effect"
            )
        if self.approval_settlement is not None:
            if not isinstance(self.approval_settlement, ApprovalSettlement):
                raise TypeError(
                    "approval_settlement must be ApprovalSettlement or None"
                )
            _validate_settlement_binding(
                self.engine_request.state_version,
                self.approval_settlement,
                owner_epoch=self.owner_epoch,
            )
            if not self.approval_settlement.approved:
                raise ValueError(
                    "dispatch authorization cannot carry an approval denial"
                )
            if self.approval_settlement.effect_id != self.effect_plan.effect_id:
                raise ValueError("approval and plan effect_id must match")
            if self.approval_settlement.attempt_id != self.effect_plan.attempt_id:
                raise ValueError("approval and plan attempt_id must match")
            plan_identity = _effect_plan_identity(self.effect_plan)
            if self.approval_settlement.tool_call_id != plan_identity[2]:
                raise ValueError("approval and plan tool_call_id must match")
            if self.approval_settlement.tool_name != plan_identity[3]:
                raise ValueError("approval and plan tool_name must match")


@dataclass(frozen=True, slots=True)
class CommitReconciliationRequest:
    session_id: str
    owner_id: str
    owner_epoch: int
    state_version: OperationStateVersion
    record: ReconciliationRecord

    def __post_init__(self) -> None:
        _require_non_empty("session_id", self.session_id)
        _require_non_empty("owner_id", self.owner_id)
        _require_positive_int("owner_epoch", self.owner_epoch)
        if not isinstance(self.state_version, OperationStateVersion):
            raise TypeError("state_version must be an OperationStateVersion")
        if not isinstance(self.record, ReconciliationRecord):
            raise TypeError("record must be a ReconciliationRecord")
        if self.record.owner_epoch != self.owner_epoch:
            raise ValueError("reconciliation owner_epoch must match request")


def _validate_commit_request(
    *,
    session_id: str,
    owner_id: str,
    owner_epoch: int,
    engine_request: EngineStepRequest,
    proposal: TransitionProposal,
) -> None:
    _require_non_empty("session_id", session_id)
    _require_non_empty("owner_id", owner_id)
    _require_positive_int("owner_epoch", owner_epoch)
    if not isinstance(engine_request, EngineStepRequest):
        raise TypeError("engine_request must be an EngineStepRequest")
    if not isinstance(proposal, TransitionProposal):
        raise TypeError("proposal must be a TransitionProposal")


class DispatchPermit:
    """Opaque single-use capability issued by a successful authorization commit."""

    __slots__ = (
        "__opaque_token",
        "_attempt_id",
        "_authorization_transition_id",
        "_claimed",
        "_effect_id",
        "_idempotency_key",
        "_owner_epoch",
        "_session_id",
    )

    def __init__(
        self,
        *,
        opaque_token: str,
        session_id: str,
        effect_id: str,
        attempt_id: str,
        authorization_transition_id: str,
        owner_epoch: int,
        idempotency_key: str | None,
    ) -> None:
        _require_non_empty("opaque_token", opaque_token)
        _require_non_empty("session_id", session_id)
        _require_non_empty("effect_id", effect_id)
        _require_non_empty("attempt_id", attempt_id)
        _require_non_empty(
            "authorization_transition_id",
            authorization_transition_id,
        )
        _require_positive_int("owner_epoch", owner_epoch)
        if idempotency_key is not None:
            _require_non_empty("idempotency_key", idempotency_key)
        self.__opaque_token = opaque_token
        self._session_id = session_id
        self._effect_id = effect_id
        self._attempt_id = attempt_id
        self._authorization_transition_id = authorization_transition_id
        self._owner_epoch = owner_epoch
        self._idempotency_key = idempotency_key
        self._claimed = False

    @classmethod
    def issue(
        cls,
        *,
        opaque_token: str,
        session_id: str,
        effect_id: str,
        attempt_id: str,
        authorization_transition_id: str,
        owner_epoch: int,
        idempotency_key: str | None,
    ) -> DispatchPermit:
        return cls(
            opaque_token=opaque_token,
            session_id=session_id,
            effect_id=effect_id,
            attempt_id=attempt_id,
            authorization_transition_id=authorization_transition_id,
            owner_epoch=owner_epoch,
            idempotency_key=idempotency_key,
        )

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def effect_id(self) -> str:
        return self._effect_id

    @property
    def attempt_id(self) -> str:
        return self._attempt_id

    @property
    def authorization_transition_id(self) -> str:
        return self._authorization_transition_id

    @property
    def owner_epoch(self) -> int:
        return self._owner_epoch

    @property
    def idempotency_key(self) -> str | None:
        return self._idempotency_key

    @property
    def claimed(self) -> bool:
        return self._claimed

    def claim(self) -> None:
        if self._claimed:
            raise RuntimeError("dispatch permit is already claimed")
        self._claimed = True

    def __repr__(self) -> str:
        return (
            "DispatchPermit("
            f"session_id={self.session_id!r}, effect_id={self.effect_id!r}, "
            f"attempt_id={self.attempt_id!r}, "
            f"authorization_transition_id={self.authorization_transition_id!r}, "
            f"owner_epoch={self.owner_epoch!r}, "
            f"idempotency_key={self.idempotency_key!r}, claimed={self.claimed!r})"
        )


class CommitResultKind(StrEnum):
    COMMITTED = "committed"
    EXACT_REPLAY = "exact_replay"
    CAS_CONFLICT = "cas_conflict"
    STALE_OWNER = "stale_owner"
    STALE_MAILBOX_CUT = "stale_mailbox_cut"
    INVALID_TRANSITION = "invalid_transition"
    STORAGE_FAILURE = "storage_failure"


@dataclass(frozen=True, slots=True)
class FailureReport:
    code: str
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty("code", self.code)
        _require_non_empty("message", self.message)
        if not self.message.strip():
            raise ValueError("message must not be blank")
        object.__setattr__(
            self,
            "details",
            _freeze_mapping(self.details, field_name="details"),
        )


@dataclass(frozen=True, slots=True)
class CommittedCommitResult:
    state_version: OperationStateVersion
    notices: tuple[CommittedFactNotice, ...] = ()
    receipt: TransitionReceipt | None = None
    kind: CommitResultKind = field(default=CommitResultKind.COMMITTED, init=False)

    def __post_init__(self) -> None:
        notices = _validate_success_result(
            self.state_version,
            self.notices,
            self.receipt,
        )
        object.__setattr__(self, "notices", notices)


@dataclass(frozen=True, slots=True)
class DispatchAuthorizedResult:
    state_version: OperationStateVersion
    permit: DispatchPermit
    notices: tuple[CommittedFactNotice, ...] = ()
    receipt: TransitionReceipt | None = None
    kind: CommitResultKind = field(default=CommitResultKind.COMMITTED, init=False)

    def __post_init__(self) -> None:
        notices = _validate_success_result(
            self.state_version,
            self.notices,
            self.receipt,
        )
        object.__setattr__(self, "notices", notices)
        if not isinstance(self.permit, DispatchPermit):
            raise TypeError("permit must be a DispatchPermit")


@dataclass(frozen=True, slots=True)
class ExactReplayCommitResult:
    state_version: OperationStateVersion
    receipt: TransitionReceipt
    kind: CommitResultKind = field(default=CommitResultKind.EXACT_REPLAY, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.state_version, OperationStateVersion):
            raise TypeError("state_version must be an OperationStateVersion")
        if not isinstance(self.receipt, TransitionReceipt):
            raise TypeError("receipt must be a TransitionReceipt")


@dataclass(frozen=True, slots=True)
class CASConflictCommitResult:
    current_state: OperationStateVersion
    kind: CommitResultKind = field(default=CommitResultKind.CAS_CONFLICT, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.current_state, OperationStateVersion):
            raise TypeError("current_state must be an OperationStateVersion")


@dataclass(frozen=True, slots=True)
class StaleOwnerCommitResult:
    expected_owner_epoch: int
    current_owner_epoch: int
    kind: CommitResultKind = field(default=CommitResultKind.STALE_OWNER, init=False)

    def __post_init__(self) -> None:
        _require_positive_int("expected_owner_epoch", self.expected_owner_epoch)
        _require_positive_int("current_owner_epoch", self.current_owner_epoch)


@dataclass(frozen=True, slots=True)
class StaleMailboxCutCommitResult:
    expected_mailbox_cut: int
    current_mailbox_cut: int
    kind: CommitResultKind = field(
        default=CommitResultKind.STALE_MAILBOX_CUT, init=False
    )

    def __post_init__(self) -> None:
        _require_non_negative_int("expected_mailbox_cut", self.expected_mailbox_cut)
        _require_non_negative_int("current_mailbox_cut", self.current_mailbox_cut)


@dataclass(frozen=True, slots=True)
class InvalidTransitionCommitResult:
    reason_code: str
    message: str
    kind: CommitResultKind = field(
        default=CommitResultKind.INVALID_TRANSITION, init=False
    )

    def __post_init__(self) -> None:
        _require_non_empty("reason_code", self.reason_code)
        _require_non_empty("message", self.message)
        if not self.message.strip():
            raise ValueError("message must not be blank")


@dataclass(frozen=True, slots=True)
class StorageFailureCommitResult:
    error: FailureReport
    kind: CommitResultKind = field(default=CommitResultKind.STORAGE_FAILURE, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.error, FailureReport):
            raise TypeError("error must be a FailureReport")


def _validate_success_result(
    state_version: OperationStateVersion,
    notices: tuple[CommittedFactNotice, ...],
    receipt: TransitionReceipt | None,
) -> tuple[CommittedFactNotice, ...]:
    if not isinstance(state_version, OperationStateVersion):
        raise TypeError("state_version must be an OperationStateVersion")
    frozen_notices = tuple(notices)
    if any(not isinstance(notice, CommittedFactNotice) for notice in frozen_notices):
        raise TypeError("notices must contain CommittedFactNotice values")
    if receipt is not None and not isinstance(receipt, TransitionReceipt):
        raise TypeError("receipt must be a TransitionReceipt or None")
    return frozen_notices


CommitTransitionResult: TypeAlias = (
    CommittedCommitResult
    | ExactReplayCommitResult
    | CASConflictCommitResult
    | StaleOwnerCommitResult
    | StaleMailboxCutCommitResult
    | InvalidTransitionCommitResult
    | StorageFailureCommitResult
)
CommitSettlementResult: TypeAlias = (
    CommittedCommitResult
    | ExactReplayCommitResult
    | CASConflictCommitResult
    | StaleOwnerCommitResult
    | StaleMailboxCutCommitResult
    | InvalidTransitionCommitResult
    | StorageFailureCommitResult
)
CommitReconciliationResult: TypeAlias = (
    CommittedCommitResult
    | ExactReplayCommitResult
    | CASConflictCommitResult
    | StaleOwnerCommitResult
    | StaleMailboxCutCommitResult
    | InvalidTransitionCommitResult
    | StorageFailureCommitResult
)
DispatchAuthorizationResult: TypeAlias = (
    DispatchAuthorizedResult
    | ExactReplayCommitResult
    | CASConflictCommitResult
    | StaleOwnerCommitResult
    | StaleMailboxCutCommitResult
    | InvalidTransitionCommitResult
    | StorageFailureCommitResult
)


@dataclass(frozen=True, slots=True)
class EffectCompletedResult:
    result: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "result",
            _freeze_mapping(self.result, field_name="result"),
        )


@dataclass(frozen=True, slots=True)
class EffectFailedResult:
    error: FailureReport

    def __post_init__(self) -> None:
        if not isinstance(self.error, FailureReport):
            raise TypeError("error must be a FailureReport")


@dataclass(frozen=True, slots=True)
class EffectIndeterminateResult:
    reason_code: str
    message: str
    evidence_ref: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty("reason_code", self.reason_code)
        _require_non_empty("message", self.message)
        if not self.message.strip():
            raise ValueError("message must not be blank")
        if self.evidence_ref is not None:
            _require_non_empty("evidence_ref", self.evidence_ref)


EffectExecutionResult: TypeAlias = (
    EffectCompletedResult | EffectFailedResult | EffectIndeterminateResult
)


@dataclass(frozen=True, order=True, slots=True)
class ControlGeneration:
    value: int

    def __post_init__(self) -> None:
        _require_non_negative_int("value", self.value)


@dataclass(frozen=True, slots=True)
class ControlSnapshot:
    generation: ControlGeneration
    raised: bool
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.generation, ControlGeneration):
            raise TypeError("generation must be a ControlGeneration")
        if not isinstance(self.raised, bool):
            raise TypeError("raised must be a bool")
        if self.raised:
            if self.reason is None:
                raise ValueError("raised control snapshot requires a reason")
            _require_non_empty("reason", self.reason)
        elif self.reason is not None:
            raise ValueError("unraised control snapshot cannot carry a reason")


class SegmentOutcomeKind(StrEnum):
    COMPLETED = "completed"
    BLOCKED = "blocked"
    SAFE_YIELD = "safe_yield"
    CANCELLED = "cancelled"
    ROUND_LIMIT = "round_limit"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CompletedOutcome:
    state_version: OperationStateVersion
    final_message: str | None
    steps_taken: int
    stop_reason: str
    kind: SegmentOutcomeKind = field(default=SegmentOutcomeKind.COMPLETED, init=False)

    def __post_init__(self) -> None:
        _validate_outcome_state(self.state_version, self.steps_taken)
        if self.final_message is not None and not isinstance(self.final_message, str):
            raise TypeError("final_message must be a string or None")
        _require_non_empty("stop_reason", self.stop_reason)


@dataclass(frozen=True, slots=True)
class BlockedOutcome:
    state_version: OperationStateVersion
    reason: str
    effect: EffectReference | None
    steps_taken: int
    kind: SegmentOutcomeKind = field(default=SegmentOutcomeKind.BLOCKED, init=False)

    def __post_init__(self) -> None:
        _validate_outcome_state(self.state_version, self.steps_taken)
        _require_non_empty("reason", self.reason)
        if self.effect is not None and not isinstance(self.effect, EffectReference):
            raise TypeError("effect must be EffectReference or None")


@dataclass(frozen=True, slots=True)
class SafeYieldOutcome:
    state_version: OperationStateVersion
    reason: str
    steps_taken: int
    kind: SegmentOutcomeKind = field(default=SegmentOutcomeKind.SAFE_YIELD, init=False)

    def __post_init__(self) -> None:
        _validate_outcome_state(self.state_version, self.steps_taken)
        _require_non_empty("reason", self.reason)


@dataclass(frozen=True, slots=True)
class CancelledOutcome:
    state_version: OperationStateVersion
    command_disposition: CommandDisposition
    steps_taken: int
    kind: SegmentOutcomeKind = field(default=SegmentOutcomeKind.CANCELLED, init=False)

    def __post_init__(self) -> None:
        _validate_outcome_state(self.state_version, self.steps_taken)
        if not isinstance(
            self.command_disposition,
            (
                AppliedCommandDisposition,
                RejectedCommandDisposition,
                SupersededCommandDisposition,
            ),
        ):
            raise TypeError("command_disposition must be a CommandDisposition")


@dataclass(frozen=True, slots=True)
class RoundLimitOutcome:
    state_version: OperationStateVersion
    steps_taken: int
    kind: SegmentOutcomeKind = field(default=SegmentOutcomeKind.ROUND_LIMIT, init=False)

    def __post_init__(self) -> None:
        _validate_outcome_state(self.state_version, self.steps_taken)


@dataclass(frozen=True, slots=True)
class FailedOutcome:
    state_version: OperationStateVersion | None
    error: FailureReport
    steps_taken: int
    kind: SegmentOutcomeKind = field(default=SegmentOutcomeKind.FAILED, init=False)

    def __post_init__(self) -> None:
        if self.state_version is not None and not isinstance(
            self.state_version,
            OperationStateVersion,
        ):
            raise TypeError("state_version must be OperationStateVersion or None")
        _require_non_negative_int("steps_taken", self.steps_taken)
        if not isinstance(self.error, FailureReport):
            raise TypeError("error must be a FailureReport")


def _validate_outcome_state(
    state_version: OperationStateVersion,
    steps_taken: int,
) -> None:
    if not isinstance(state_version, OperationStateVersion):
        raise TypeError("state_version must be an OperationStateVersion")
    _require_non_negative_int("steps_taken", steps_taken)


SegmentOutcome: TypeAlias = (
    CompletedOutcome
    | BlockedOutcome
    | SafeYieldOutcome
    | CancelledOutcome
    | RoundLimitOutcome
    | FailedOutcome
)


@dataclass(frozen=True, slots=True)
class DeliveryFailure:
    sink: str
    message: str

    def __post_init__(self) -> None:
        _require_non_empty("sink", self.sink)
        _require_non_empty("message", self.message)
        if not self.message.strip():
            raise ValueError("message must not be blank")


class CancellationToken(Protocol):
    @property
    def cancelled(self) -> bool: ...

    async def wait(self) -> None: ...


class FrameSink(Protocol):
    async def emit(self, frame: StreamFrame) -> None: ...


class CommittedFactSink(Protocol):
    async def emit(self, notice: CommittedFactNotice) -> None: ...


class ModelAdapter(Protocol):
    async def generate(
        self,
        request: ModelRequest,
        frame_sink: FrameSink,
        cancellation: CancellationToken,
    ) -> ModelGenerationResult: ...


class CommitPort(Protocol):
    async def commit_transition(
        self,
        request: CommitTransitionRequest,
    ) -> CommitTransitionResult: ...

    async def authorize_dispatch(
        self,
        request: DispatchAuthorizationRequest,
    ) -> DispatchAuthorizationResult: ...

    async def commit_settlement(
        self,
        request: CommitSettlementRequest,
    ) -> CommitSettlementResult: ...

    async def commit_reconciliation(
        self,
        request: CommitReconciliationRequest,
    ) -> CommitReconciliationResult: ...


class EffectExecutor(Protocol):
    async def execute(
        self,
        permit: DispatchPermit,
        cancellation: CancellationToken,
    ) -> EffectExecutionResult: ...


class ControlProbe(Protocol):
    def observe(self) -> ControlSnapshot: ...

    async def wait(self, after: ControlGeneration) -> ControlSnapshot: ...
