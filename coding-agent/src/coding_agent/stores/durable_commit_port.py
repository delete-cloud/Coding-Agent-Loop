"""Concrete AgentKit commit ports backed by authoritative durable stores."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast

from agentkit.runtime.contracts import (
    CASConflictCommitResult,
    CommitReconciliationRequest,
    CommitReconciliationResult,
    CommitSettlementRequest,
    CommitSettlementResult,
    CommitTransitionRequest,
    CommitTransitionResult,
    CommittedCommitResult,
    CommittedFactNotice,
    DispatchAuthorizationRequest,
    DispatchAuthorizationResult,
    DispatchAuthorizedResult,
    DispatchPermit,
    EffectMutation,
    EffectPlan,
    EffectStatus,
    EffectSettlement,
    EffectSettled,
    EffectSettlementOutcome,
    ExactReplayCommitResult,
    FailureReport,
    InvalidTransitionCommitResult,
    OperationStateVersion,
    StaleMailboxCutCommitResult,
    StaleOwnerCommitResult,
    TransitionReceipt,
    StorageFailureCommitResult,
    TerminalAction,
)
from coding_agent.server.stores.session_owner_store import (
    OwnerAuthority,
    SessionOwnershipConflictError,
)
from coding_agent.stores.runtime_store import (
    AuthoritativeCommit,
    AuthoritativeUnitOfWork,
    ChildBindingConflictError,
    ChildExecutionBinding,
    CommandDispositionConflictError,
    EffectMutationConflictError,
    EffectReconciliationEvidence,
    EventRecord,
    EffectLedgerSlot,
    ExecutorAttemptRecord,
    ExecutorAttemptConflictError,
    InvalidDispatchAuthorizationError,
    InvalidReconciliationPreconditionError,
    JSONObject,
    RecoveryEvidenceConflictError,
    RecoveryTransitionGuard,
    RecoveryGuardKind,
    StaleRecoveryGuardError,
    StateVersionConflictError,
    StaleMailboxCutError,
    TransitionFingerprintMismatchError,
    UnstartedDispatchCloseoutGuard,
    parse_u64,
    state_value_with_reconciled_effect,
)


class _DurableCommitStore(Protocol):
    async def commit_authoritative_uow(
        self,
        authority: OwnerAuthority,
        unit: AuthoritativeUnitOfWork,
    ) -> AuthoritativeCommit: ...

    async def load_operation_state(
        self,
        session_id: str,
        run_id: str,
    ) -> OperationStateVersion | None: ...

    async def load_effect_slot(
        self,
        session_id: str,
        effect_id: str,
    ) -> EffectLedgerSlot | None: ...
    async def load_effect_reconciliation_evidence(
        self,
        session_id: str,
        evidence_ref: str,
    ) -> EffectReconciliationEvidence | None: ...

    async def load_child_execution_binding(
        self,
        session_id: str,
        *,
        parent_effect_id: str | None = None,
        child_run_id: str | None = None,
    ) -> ChildExecutionBinding | None: ...

    async def load_transition_receipt(
        self,
        session_id: str,
        projection_epoch: int,
        transition_id: str,
    ) -> TransitionReceipt | None: ...
    async def load_executor_attempt(
        self,
        session_id: str,
        effect_id: str,
        attempt_id: str,
        authorization_transition_id: str,
    ) -> ExecutorAttemptRecord | None: ...


@dataclass(frozen=True, slots=True)
class AuthorizationReplayMarker:
    session_id: str
    run_id: str
    authorization_transition_id: str
    effect_id: str
    attempt_id: str
    owner_epoch: int
    authorization_state: OperationStateVersion


@dataclass(frozen=True, slots=True)
class AuthorizationReplayRecovery:
    state_version: OperationStateVersion
    step_input: EffectSettled


class _DurableCommitPort:
    """Shared conversion and error mapping for both durable store bindings."""

    __slots__ = (
        "_clock",
        "_permit_token_factory",
        "_session_state",
        "_authorization_replay_markers",
        "_store",
    )

    def __init__(
        self,
        store: _DurableCommitStore,
        *,
        session_state: Mapping[str, object],
        clock: Callable[[], datetime] | None = None,
        permit_token_factory: Callable[[], str] | None = None,
    ) -> None:
        session_id = session_state.get("id")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_state.id must be a non-empty string")
        payload_session_id = session_state.get("session_id")
        if payload_session_id not in {None, session_id}:
            raise ValueError("session_state.session_id must match session_state.id")
        self._store = store
        self._session_state = cast(JSONObject, dict(session_state))
        self._clock = clock or (lambda: datetime.now(UTC))
        self._permit_token_factory = permit_token_factory or (
            lambda: secrets.token_urlsafe(32)
        )
        self._authorization_replay_markers: dict[
            tuple[str, str],
            AuthorizationReplayMarker,
        ] = {}

    async def commit_transition(
        self,
        request: CommitTransitionRequest,
    ) -> CommitTransitionResult:
        unit = self._transition_unit(
            request,
            effect_mutations=tuple(
                EffectMutation.prepare(plan) for plan in request.proposal.effect_plans
            ),
            create_child_bindings=True,
        )
        commit = await self._commit(request, unit)
        return self._ordinary_result(commit)

    async def commit_transition_with_recovery_guard(
        self,
        request: CommitTransitionRequest,
        guard: RecoveryTransitionGuard,
    ) -> CommitTransitionResult:
        unit = self._transition_unit(
            request,
            effect_mutations=tuple(
                EffectMutation.prepare(plan) for plan in request.proposal.effect_plans
            ),
            create_child_bindings=True,
            recovery_guard=guard,
            transition_id=(
                f"{request.proposal.transition_id}:recovery:"
                f"{guard.lease_id}:{guard.resume_generation}:"
                f"{guard.expected_recovery_cut}"
            ),
            terminal_action=guard.kind is RecoveryGuardKind.CHILD_TERMINAL,
        )
        commit = await self._commit(request, unit)
        return self._ordinary_result(commit)

    async def authorize_dispatch(
        self,
        request: DispatchAuthorizationRequest,
    ) -> DispatchAuthorizationResult:
        unit = self._transition_unit(
            request,
            effect_mutations=(request.effect_mutation,),
            create_child_bindings=False,
            expected_mailbox_cut=str(request.mailbox_cut),
        )
        commit = await self._commit(request, unit)
        if not isinstance(commit, AuthoritativeCommit):
            return commit
        if commit.idempotent:
            state_version, _receipt = _required_transition_result(commit)
            marker = AuthorizationReplayMarker(
                session_id=request.session_id,
                run_id=request.engine_request.state_version.run_id,
                authorization_transition_id=request.proposal.transition_id,
                effect_id=request.effect_plan.effect_id,
                attempt_id=request.effect_plan.attempt_id,
                owner_epoch=request.owner_epoch,
                authorization_state=state_version,
            )
            self._authorization_replay_markers[(marker.session_id, marker.run_id)] = (
                marker
            )
            return self._exact_replay(commit)
        state_version, receipt = _required_transition_result(commit)
        permit_token = self._permit_token_factory()
        if not isinstance(permit_token, str) or not permit_token:
            return StorageFailureCommitResult(
                error=FailureReport(
                    code="invalid_permit_token",
                    message="permit token factory returned an empty token",
                )
            )
        return DispatchAuthorizedResult(
            state_version=state_version,
            permit=DispatchPermit.issue(
                opaque_token=permit_token,
                session_id=request.session_id,
                effect_id=request.effect_plan.effect_id,
                attempt_id=request.effect_plan.attempt_id,
                authorization_transition_id=request.proposal.transition_id,
                owner_epoch=request.owner_epoch,
                idempotency_key=request.effect_plan.idempotency_key,
            ),
            notices=_committed_notices(commit),
            receipt=receipt,
        )

    def consume_authorization_replay_marker(
        self,
        request: object,
    ) -> AuthorizationReplayMarker | None:
        session_id = getattr(request, "session_id", None)
        state_version = getattr(request, "state_version", None)
        run_id = getattr(state_version, "run_id", None)
        if not isinstance(session_id, str) or not isinstance(run_id, str):
            raise TypeError("runner request must identify a session and run")
        marker = self._authorization_replay_markers.pop(
            (session_id, run_id),
            None,
        )
        if marker is None:
            return None
        owner_epoch = getattr(request, "owner_epoch", None)
        if owner_epoch != marker.owner_epoch:
            raise ValueError("authorization replay marker owner epoch changed")
        return marker

    async def recover_authorization_replay(
        self,
        marker: AuthorizationReplayMarker,
    ) -> AuthorizationReplayRecovery:
        current_state = await self._store.load_operation_state(
            marker.session_id,
            marker.run_id,
        )
        if current_state is None:
            raise RuntimeError("authorization replay operation state is missing")
        effect = await self._store.load_effect_slot(
            marker.session_id,
            marker.effect_id,
        )
        if effect is None:
            raise RuntimeError("authorization replay effect is missing")
        attempt = await self._store.load_executor_attempt(
            marker.session_id,
            marker.effect_id,
            marker.attempt_id,
            marker.authorization_transition_id,
        )
        if attempt is None:
            raise RuntimeError("authorization replay attempt is missing")
        while attempt.status in {"reserved", "started"}:
            await asyncio.sleep(0.01)
            effect = await self._store.load_effect_slot(
                marker.session_id,
                marker.effect_id,
            )
            attempt = await self._store.load_executor_attempt(
                marker.session_id,
                marker.effect_id,
                marker.attempt_id,
                marker.authorization_transition_id,
            )
            if effect is None or attempt is None:
                raise RuntimeError(
                    "authorization replay evidence disappeared while waiting"
                )
        state_version = (
            current_state
            if _active_authorization(current_state, marker)
            else marker.authorization_state
        )
        settlement = _replayed_effect_settlement(
            marker,
            state_version,
            effect,
            attempt,
        )
        return AuthorizationReplayRecovery(
            state_version=state_version,
            step_input=EffectSettled(settlement=settlement),
        )

    async def recover_authorization_without_marker(
        self,
        request: object,
    ) -> AuthorizationReplayRecovery | None:
        session_id = getattr(request, "session_id", None)
        owner_epoch = getattr(request, "owner_epoch", None)
        state_version = getattr(request, "state_version", None)
        if (
            not isinstance(session_id, str)
            or isinstance(owner_epoch, bool)
            or not isinstance(owner_epoch, int)
            or owner_epoch <= 0
            or not isinstance(state_version, OperationStateVersion)
        ):
            raise TypeError("runner request lacks durable authorization authority")
        runtime_state = state_version.value.get("_agentkit_runtime")
        if not isinstance(runtime_state, Mapping):
            return None
        active = runtime_state.get("active_effect_authorization")
        if not isinstance(active, Mapping):
            return None
        identity = tuple(
            active.get(field_name)
            for field_name in (
                "authorization_transition_id",
                "effect_id",
                "attempt_id",
            )
        )
        if any(not isinstance(value, str) or not value for value in identity):
            raise RuntimeError("active effect authorization identity is incomplete")
        authorization_transition_id, effect_id, attempt_id = cast(
            tuple[str, str, str],
            identity,
        )
        return await self.recover_authorization_replay(
            AuthorizationReplayMarker(
                session_id=session_id,
                run_id=state_version.run_id,
                authorization_transition_id=authorization_transition_id,
                effect_id=effect_id,
                attempt_id=attempt_id,
                owner_epoch=owner_epoch,
                authorization_state=state_version,
            )
        )

    async def commit_settlement(
        self,
        request: CommitSettlementRequest,
    ) -> CommitSettlementResult:
        try:
            (
                transition_id,
                adopt_transition_ids,
            ) = await self._parent_settlement_transition_ids(request)
            closeout_guard = await self._unstarted_closeout_guard(request)
        except Exception as exc:
            return _storage_failure(exc)
        preparations = tuple(
            EffectMutation.prepare(plan) for plan in request.proposal.effect_plans
        )
        unit = self._transition_unit(
            request,
            effect_mutations=(request.effect_mutation, *preparations),
            create_child_bindings=True,
            transition_id=transition_id,
            unstarted_dispatch_closeout=closeout_guard,
            adopt_transition_ids=adopt_transition_ids,
        )
        commit = await self._commit(request, unit)
        return self._ordinary_result(commit)

    async def commit_settlement_with_recovery_guard(
        self,
        request: CommitSettlementRequest,
        guard: RecoveryTransitionGuard,
    ) -> CommitSettlementResult:
        preparations = tuple(
            EffectMutation.prepare(plan) for plan in request.proposal.effect_plans
        )
        transition_id = request.proposal.transition_id
        adopt_transition_ids: tuple[str, ...] = ()
        if guard.kind is RecoveryGuardKind.PARENT_SETTLEMENT:
            try:
                binding = await self._store.load_child_execution_binding(
                    request.session_id,
                    child_run_id=guard.child_run_id,
                )
            except Exception as exc:
                return _storage_failure(exc)
            if binding is None:
                return InvalidTransitionCommitResult(
                    reason_code="child_binding_missing",
                    message="recovery parent settlement requires child binding",
                )
            transition_id = _recovery_parent_settlement_transition_id(
                binding,
                guard,
            )
            adopt_transition_ids = _distinct_transition_ids(
                transition_id,
                request.proposal.transition_id,
                binding.live_parent_settlement_transition_id,
            )
        else:
            transition_id = (
                f"{request.proposal.transition_id}:recovery:"
                f"{guard.lease_id}:{guard.resume_generation}:"
                f"{guard.expected_recovery_cut}"
            )
        unit = self._transition_unit(
            request,
            effect_mutations=(request.effect_mutation, *preparations),
            create_child_bindings=True,
            recovery_guard=guard,
            transition_id=transition_id,
            adopt_transition_ids=adopt_transition_ids,
            terminal_action=guard.kind is RecoveryGuardKind.CHILD_TERMINAL,
        )
        commit = await self._commit(request, unit)
        return self._ordinary_result(commit)

    async def commit_reconciliation(
        self,
        request: CommitReconciliationRequest,
    ) -> CommitReconciliationResult:
        try:
            evidence = await self._store.load_effect_reconciliation_evidence(
                request.session_id,
                request.record.evidence_ref,
            )
        except Exception as exc:
            return _storage_failure(exc)
        if evidence is None:
            return InvalidTransitionCommitResult(
                reason_code="missing_reconciliation_evidence",
                message="durable reconciliation evidence does not exist",
            )
        mutation = EffectMutation(
            effect_id=request.record.effect_id,
            attempt_id=request.record.attempt_id,
            expected_status=EffectStatus.UNKNOWN,
            status=EffectStatus(request.record.observed_outcome.value),
            payload={
                "result": evidence.result,
                "reason_code": evidence.reason_code,
                "reason_message": evidence.reason_message,
            },
            reconciliation=request.record,
        )
        unit = AuthoritativeUnitOfWork(
            event=None,
            session_state=self._session_state,
            transition_id=request.record.transition_id,
            state_cas=request.state_version.cas,
            state_value=state_value_with_reconciled_effect(
                request.state_version.value,
                evidence,
                request.record,
            ),
            effect_mutations=(mutation,),
            expected_reconciliation_authorization_transition_id=(
                evidence.authorization_transition_id
            ),
            reconciliation_evidence_ref=evidence.evidence_ref,
        )
        commit = await self._commit(request, unit)
        return self._ordinary_result(commit)

    def _transition_unit(
        self,
        request: CommitTransitionRequest
        | CommitSettlementRequest
        | DispatchAuthorizationRequest,
        *,
        effect_mutations: tuple[EffectMutation, ...],
        create_child_bindings: bool,
        expected_mailbox_cut: str | None = None,
        recovery_guard: RecoveryTransitionGuard | None = None,
        transition_id: str | None = None,
        adopt_transition_ids: tuple[str, ...] = (),
        terminal_action: bool | None = None,
        unstarted_dispatch_closeout: UnstartedDispatchCloseoutGuard | None = None,
    ) -> AuthoritativeUnitOfWork:
        facts = tuple(
            EventRecord(
                event_id=fact.fact_id,
                session_id=request.session_id,
                event_kind=fact.fact_kind,
                payload=cast(JSONObject, dict(fact.payload)),
                created_at=self._clock(),
            )
            for fact in request.proposal.pending_facts
        )
        return AuthoritativeUnitOfWork(
            event=None,
            session_state=self._session_state,
            transition_id=transition_id or request.proposal.transition_id,
            state_cas=request.engine_request.state_version.cas,
            adopt_transition_ids=adopt_transition_ids,
            state_value=request.proposal.state_value,
            facts=facts,
            dispositions=request.proposal.dispositions,
            effect_mutations=effect_mutations,
            effect_plans=request.proposal.effect_plans,
            child_bindings=(
                _child_bindings(
                    session_id=request.session_id,
                    parent_run_id=request.engine_request.state_version.run_id,
                    prepared_transition_id=request.proposal.transition_id,
                    plans=request.proposal.effect_plans,
                )
                if create_child_bindings
                else ()
            ),
            expected_mailbox_cut=expected_mailbox_cut,
            recovery_guard=recovery_guard,
            terminal_action=(
                isinstance(request.proposal.next_action, TerminalAction)
                if terminal_action is None
                else terminal_action
            ),
            unstarted_dispatch_closeout=unstarted_dispatch_closeout,
        )

    async def _parent_settlement_transition_ids(
        self,
        request: CommitSettlementRequest,
    ) -> tuple[str, tuple[str, ...]]:
        binding = await self._store.load_child_execution_binding(
            request.session_id,
            parent_effect_id=request.settlement.effect_id,
        )
        if binding is None:
            return request.proposal.transition_id, ()
        if binding.parent_attempt_id != request.settlement.attempt_id:
            raise ValueError("parent settlement attempt does not match child binding")
        live_transition_id = binding.live_parent_settlement_transition_id
        candidates = [request.proposal.transition_id]
        lease = binding.active_lease
        if lease is not None:
            candidates.append(
                _recovery_parent_settlement_transition_id(
                    binding,
                    RecoveryTransitionGuard(
                        lease_id=lease.lease_id,
                        child_run_id=lease.child_run_id,
                        resume_generation=lease.resume_generation,
                        expected_recovery_cut=lease.resume_cut,
                        kind=RecoveryGuardKind.PARENT_SETTLEMENT,
                    ),
                )
            )
        return live_transition_id, _distinct_transition_ids(
            live_transition_id,
            *candidates,
        )

    async def _unstarted_closeout_guard(
        self,
        request: CommitSettlementRequest,
    ) -> UnstartedDispatchCloseoutGuard | None:
        settlement = request.settlement
        mutation = request.effect_mutation
        if (
            not isinstance(settlement, EffectSettlement)
            or mutation.expected_status is not EffectStatus.DISPATCHED
            or mutation.status is not EffectStatus.UNKNOWN
        ):
            return None
        attempt = await self._store.load_executor_attempt(
            request.session_id,
            settlement.effect_id,
            settlement.attempt_id,
            settlement.authorization_transition_id,
        )
        if attempt is None or attempt.status != "authorized_unclaimed":
            return None
        return UnstartedDispatchCloseoutGuard(
            effect_id=settlement.effect_id,
            attempt_id=settlement.attempt_id,
            authorization_transition_id=settlement.authorization_transition_id,
            executor_id="coordinator-unstarted",
            evidence_ref=f"{request.proposal.transition_id}:unstarted-control",
            closed_at=self._clock(),
        )

    async def _commit(
        self,
        request: CommitTransitionRequest
        | CommitSettlementRequest
        | DispatchAuthorizationRequest
        | CommitReconciliationRequest,
        unit: AuthoritativeUnitOfWork,
    ) -> AuthoritativeCommit | CommitTransitionResult:
        authority = OwnerAuthority(
            session_id=request.session_id,
            owner_id=request.owner_id,
            epoch=request.owner_epoch,
        )
        try:
            return await self._store.commit_authoritative_uow(authority, unit)
        except StaleMailboxCutError as exc:
            return StaleMailboxCutCommitResult(
                expected_mailbox_cut=exc.expected_mailbox_cut,
                current_mailbox_cut=exc.current_mailbox_cut,
            )
        except StateVersionConflictError:
            try:
                current = await self._store.load_operation_state(
                    request.session_id,
                    unit.state_cas.run_id if unit.state_cas is not None else "",
                )
            except Exception as exc:
                return _storage_failure(exc)
            if current is None:
                return InvalidTransitionCommitResult(
                    reason_code="operation_state_missing",
                    message="current operation state does not exist",
                )
            return CASConflictCommitResult(current_state=current)
        except SessionOwnershipConflictError as exc:
            current_epoch = getattr(exc, "current_owner_epoch", request.owner_epoch)
            if (
                isinstance(current_epoch, bool)
                or not isinstance(current_epoch, int)
                or current_epoch <= 0
            ):
                current_epoch = request.owner_epoch
            return StaleOwnerCommitResult(
                expected_owner_epoch=request.owner_epoch,
                current_owner_epoch=current_epoch,
            )
        except (
            CommandDispositionConflictError,
            ChildBindingConflictError,
            EffectMutationConflictError,
            ExecutorAttemptConflictError,
            InvalidDispatchAuthorizationError,
            InvalidReconciliationPreconditionError,
            RecoveryEvidenceConflictError,
            TransitionFingerprintMismatchError,
            StaleRecoveryGuardError,
            ValueError,
        ) as exc:
            return InvalidTransitionCommitResult(
                reason_code=_reason_code(exc),
                message=_exception_message(exc),
            )
        except Exception as exc:
            return _storage_failure(exc)

    @staticmethod
    def _ordinary_result(
        commit: AuthoritativeCommit | CommitTransitionResult,
    ) -> CommitTransitionResult:
        if not isinstance(commit, AuthoritativeCommit):
            return commit
        if commit.idempotent:
            return _DurableCommitPort._exact_replay(commit)
        state_version, receipt = _required_transition_result(commit)
        return CommittedCommitResult(
            state_version=state_version,
            notices=_committed_notices(commit),
            receipt=receipt,
        )

    @staticmethod
    def _exact_replay(commit: AuthoritativeCommit) -> ExactReplayCommitResult:
        state_version, receipt = _required_transition_result(commit)
        return ExactReplayCommitResult(
            state_version=state_version,
            receipt=receipt,
        )


def _active_authorization(
    state_version: OperationStateVersion,
    marker: AuthorizationReplayMarker,
) -> bool:
    runtime_state = state_version.value.get("_agentkit_runtime")
    if not isinstance(runtime_state, Mapping):
        return False
    active = runtime_state.get("active_effect_authorization")
    return (
        isinstance(active, Mapping)
        and active.get("effect_id") == marker.effect_id
        and active.get("attempt_id") == marker.attempt_id
        and active.get("authorization_transition_id")
        == marker.authorization_transition_id
    )


def _replayed_effect_settlement(
    marker: AuthorizationReplayMarker,
    state_version: OperationStateVersion,
    effect: EffectLedgerSlot,
    attempt: ExecutorAttemptRecord,
) -> EffectSettlement:
    runtime_state = state_version.value.get("_agentkit_runtime")
    if not isinstance(runtime_state, Mapping):
        raise RuntimeError("authorization replay state lacks runtime metadata")
    active = runtime_state.get("active_effect_authorization")
    if not isinstance(active, Mapping):
        raise RuntimeError("authorization replay state lacks active effect")
    tool_call_id = active.get("tool_call_id")
    tool_name = active.get("tool_name")
    if not isinstance(tool_call_id, str) or not tool_call_id:
        raise RuntimeError("authorization replay tool call identity is missing")
    if not isinstance(tool_name, str) or not tool_name:
        raise RuntimeError("authorization replay tool name is missing")
    common = {
        "input_id": (
            f"{marker.authorization_transition_id}:settlement:{marker.attempt_id}"
        ),
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "effect_id": marker.effect_id,
        "attempt_id": marker.attempt_id,
        "authorization_transition_id": marker.authorization_transition_id,
        "owner_epoch": marker.owner_epoch,
    }
    if effect.status == EffectStatus.COMPLETED.value:
        return EffectSettlement(
            **common,
            outcome=EffectSettlementOutcome.COMPLETED,
            result=effect.payload.get("result", {}),
        )
    if effect.status == EffectStatus.FAILED.value:
        return EffectSettlement(
            **common,
            outcome=EffectSettlementOutcome.FAILED,
            result=effect.payload.get("result", {}),
            reason_code=_nonempty_payload_text(
                effect.payload,
                "reason_code",
                "effect_failed",
            ),
            reason_message=_nonempty_payload_text(
                effect.payload,
                "reason_message",
                "durable effect failed",
            ),
        )
    if effect.status == EffectStatus.UNKNOWN.value:
        return EffectSettlement(
            **common,
            outcome=EffectSettlementOutcome.INDETERMINATE,
            result=effect.payload.get("result", {}),
            reason_code=_nonempty_payload_text(
                effect.payload,
                "reason_code",
                "effect_indeterminate",
            ),
            reason_message=_nonempty_payload_text(
                effect.payload,
                "reason_message",
                "durable effect outcome is indeterminate",
            ),
        )
    if effect.status != EffectStatus.DISPATCHED.value:
        raise RuntimeError(
            f"authorization replay effect status is unsupported: {effect.status}"
        )
    if attempt.status in {"reserved", "started"}:
        raise RuntimeError(
            "authorization replay cannot settle an active executor attempt"
        )
    if attempt.status not in {"authorized_unclaimed", "quiescent"}:
        raise RuntimeError(
            f"authorization replay attempt status is unsupported: {attempt.status}"
        )
    return EffectSettlement(
        **common,
        outcome=EffectSettlementOutcome.INDETERMINATE,
        result={},
        reason_code="authorization_replay_unclaimed",
        reason_message="authorized effect execution could not be proven",
    )


def _nonempty_payload_text(
    payload: Mapping[str, object],
    field_name: str,
    fallback: str,
) -> str:
    value = payload.get(field_name)
    return value if isinstance(value, str) and value.strip() else fallback


def _recovery_parent_settlement_transition_id(
    binding: ChildExecutionBinding,
    guard: RecoveryTransitionGuard,
) -> str:
    return (
        f"{binding.live_parent_settlement_transition_id}:recovery:"
        f"{guard.lease_id}:{guard.resume_generation}:"
        f"{guard.expected_recovery_cut}"
    )


def _distinct_transition_ids(
    current_transition_id: str,
    *candidates: str,
) -> tuple[str, ...]:
    result: list[str] = []
    for candidate in candidates:
        if candidate != current_transition_id and candidate not in result:
            result.append(candidate)
    return tuple(result)


class SQLiteCommitPort(_DurableCommitPort):
    """Frozen AgentKit CommitPort backed by SQLite durable storage."""


class PostgreSQLCommitPort(_DurableCommitPort):
    """Frozen AgentKit CommitPort backed by PostgreSQL durable storage."""


def _required_transition_result(
    commit: AuthoritativeCommit,
) -> tuple[OperationStateVersion, TransitionReceipt]:
    state_version = commit.state_version
    receipt = commit.transition_receipt
    if state_version is None or receipt is None:
        raise RuntimeError("authoritative transition commit omitted its result")
    return state_version, receipt


def _child_bindings(
    *,
    session_id: str,
    parent_run_id: str,
    prepared_transition_id: str,
    plans: tuple[EffectPlan, ...],
) -> tuple[ChildExecutionBinding, ...]:
    from coding_agent.runs.child_execution import child_execution_binding

    return tuple(
        child_execution_binding(
            session_id=session_id,
            parent_run_id=parent_run_id,
            parent_effect_id=plan.effect_id,
            parent_attempt_id=plan.attempt_id,
            prepared_transition_id=prepared_transition_id,
        )
        for plan in plans
        if plan.payload.get("tool_name") == "subagent"
    )


def _committed_notices(
    commit: AuthoritativeCommit,
) -> tuple[CommittedFactNotice, ...]:
    return tuple(
        CommittedFactNotice(
            fact_id=fact.event_id,
            fact_kind=fact.event_kind,
            payload=fact.payload,
            session_seq=fact.session_seq,
            projection_epoch=(
                None
                if fact.projection_epoch is None
                else parse_u64(fact.projection_epoch, field_name="projection_epoch")
            ),
            event_record_id=fact.event_id,
        )
        for fact in commit.facts
    )


def _reason_code(exc: Exception) -> str:
    name = type(exc).__name__
    pieces: list[str] = []
    current = ""
    for character in name:
        if character.isupper() and current:
            pieces.append(current)
            current = character.lower()
        else:
            current += character.lower()
    if current:
        pieces.append(current)
    return "_".join(pieces) or "invalid_transition"


def _exception_message(exc: BaseException) -> str:
    message = str(exc).strip()
    return message or type(exc).__name__ or "unknown error"


def _storage_failure(exc: BaseException) -> StorageFailureCommitResult:
    return StorageFailureCommitResult(
        error=FailureReport(
            code="storage_failure",
            message=_exception_message(exc),
        )
    )
