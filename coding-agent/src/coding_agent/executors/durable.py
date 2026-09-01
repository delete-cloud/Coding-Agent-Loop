"""Durable executor-attempt fencing for AgentKit effect dispatch."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, TypeVar

from agentkit.runtime.contracts import (
    CancellationToken,
    DispatchPermit,
    EffectCompletedResult,
    EffectExecutionResult,
    EffectFailedResult,
    EffectIndeterminateResult,
    FailureReport,
)
from agentkit.tools import UNHANDLED_TOOL_RESULT
from coding_agent.executors.external import (
    ExecutorPlan,
    ExecutorResult,
    ExternalExecutor,
)
from coding_agent.server.stores.session_owner_store import OwnerAuthority
from coding_agent.stores.runtime_store import (
    EffectLedgerSlot,
    ExecutorAttemptRecord,
    JSONObject,
)


@dataclass(frozen=True, slots=True)
class DurableEffectInvocation:
    session_id: str
    effect_id: str
    attempt_id: str
    authorization_transition_id: str
    owner_epoch: int
    effect_kind: str
    payload: Mapping[str, Any]
    idempotency_key: str | None


class DurableEffectBackend(Protocol):
    async def execute(
        self,
        invocation: DurableEffectInvocation,
        cancellation: CancellationToken,
    ) -> EffectExecutionResult: ...


class DurableExecutorStore(Protocol):
    async def load_effect_slot(
        self,
        session_id: str,
        effect_id: str,
    ) -> EffectLedgerSlot | None: ...

    async def load_executor_attempt(
        self,
        session_id: str,
        effect_id: str,
        attempt_id: str,
        authorization_transition_id: str,
    ) -> ExecutorAttemptRecord | None: ...

    async def reserve_executor_attempt(
        self,
        authority: OwnerAuthority,
        *,
        effect_id: str,
        attempt_id: str,
        authorization_transition_id: str,
        executor_id: str,
        lease_expires_at: datetime,
    ) -> ExecutorAttemptRecord: ...

    async def mark_executor_attempt_started(
        self,
        authority: OwnerAuthority,
        *,
        effect_id: str,
        attempt_id: str,
        authorization_transition_id: str,
        executor_id: str,
        claim_generation: int,
        now: datetime,
    ) -> ExecutorAttemptRecord: ...

    async def quiesce_claimed_executor_attempt(
        self,
        authority: OwnerAuthority,
        *,
        effect_id: str,
        attempt_id: str,
        authorization_transition_id: str,
        executor_id: str,
        now: datetime,
        evidence_ref: str,
    ) -> ExecutorAttemptRecord: ...


class DurableEffectExecutor:
    """Validate a claimed permit and fence exactly one underlying execution."""

    __slots__ = (
        "_backend",
        "_clock",
        "_executor_id",
        "_owner_id",
        "_quiescence_evidence_factory",
        "_reservation_lease",
        "_store",
    )

    def __init__(
        self,
        store: DurableExecutorStore,
        *,
        owner_id: str,
        executor_id: str,
        backend: DurableEffectBackend,
        reservation_lease: timedelta,
        clock: Callable[[], datetime] | None = None,
        quiescence_evidence_factory: Callable[[DurableEffectInvocation, int], str]
        | None = None,
    ) -> None:
        if not owner_id:
            raise ValueError("owner_id must be non-empty")
        if not executor_id:
            raise ValueError("executor_id must be non-empty")
        if reservation_lease <= timedelta(0):
            raise ValueError("reservation_lease must be positive")
        self._store = store
        self._owner_id = owner_id
        self._executor_id = executor_id
        self._backend = backend
        self._reservation_lease = reservation_lease
        self._clock = clock or (lambda: datetime.now(UTC))
        self._quiescence_evidence_factory = quiescence_evidence_factory or (
            lambda invocation, generation: (
                f"executor:{self._executor_id}:{invocation.effect_id}:"
                f"{invocation.attempt_id}:{generation}:quiescent"
            )
        )

    def validate_permit(self, permit: DispatchPermit) -> None:
        if not isinstance(permit, DispatchPermit):
            raise TypeError("permit must be a DispatchPermit")
        if not permit.claimed:
            raise ValueError("dispatch permit must be claimed before execution")

    async def execute(
        self,
        permit: DispatchPermit,
        cancellation: CancellationToken,
    ) -> EffectExecutionResult:
        self.validate_permit(permit)
        authority = OwnerAuthority(
            session_id=permit.session_id,
            owner_id=self._owner_id,
            epoch=permit.owner_epoch,
        )
        claim_task = asyncio.create_task(self._claim_attempt(authority, permit))
        try:
            claimed, caller_cancelled = await _await_task_through_cancellation(
                claim_task
            )
        except Exception as exc:
            cleanup_error = await self._quiesce_after_claim_failure(
                authority,
                permit,
            )
            message = _exception_message(cleanup_error or exc)
            return EffectIndeterminateResult(
                reason_code="durable_executor_claim_indeterminate",
                message=message,
            )
        invocation, started = claimed
        if caller_cancelled or cancellation.cancelled:
            await self._quiesce_through_cancellation(
                authority,
                permit,
                invocation,
                started.claim_generation,
            )
            raise asyncio.CancelledError

        try:
            result = await self._backend.execute(invocation, cancellation)
            if not isinstance(
                result,
                (EffectCompletedResult, EffectFailedResult, EffectIndeterminateResult),
            ):
                result = EffectIndeterminateResult(
                    reason_code="invalid_effect_backend_result",
                    message="effect backend returned an unsupported result",
                )
        except asyncio.CancelledError:
            await self._quiesce_through_cancellation(
                authority,
                permit,
                invocation,
                started.claim_generation,
            )
            raise
        except Exception as exc:
            result = EffectIndeterminateResult(
                reason_code="effect_backend_error",
                message=_exception_message(exc),
            )

        try:
            await self._quiesce_through_cancellation(
                authority,
                permit,
                invocation,
                started.claim_generation,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return EffectIndeterminateResult(
                reason_code="executor_quiescence_failed",
                message=_exception_message(exc),
            )
        return result

    async def _claim_attempt(
        self,
        authority: OwnerAuthority,
        permit: DispatchPermit,
    ) -> tuple[DurableEffectInvocation, ExecutorAttemptRecord]:
        invocation = await self._load_invocation(permit)
        attempt = await self._store.load_executor_attempt(
            permit.session_id,
            permit.effect_id,
            permit.attempt_id,
            permit.authorization_transition_id,
        )
        _validate_authorized_attempt(attempt, permit)
        reserved = await self._store.reserve_executor_attempt(
            authority,
            effect_id=permit.effect_id,
            attempt_id=permit.attempt_id,
            authorization_transition_id=permit.authorization_transition_id,
            executor_id=self._executor_id,
            lease_expires_at=self._clock() + self._reservation_lease,
        )
        started = await self._store.mark_executor_attempt_started(
            authority,
            effect_id=permit.effect_id,
            attempt_id=permit.attempt_id,
            authorization_transition_id=permit.authorization_transition_id,
            executor_id=self._executor_id,
            claim_generation=reserved.claim_generation,
            now=self._clock(),
        )
        if started.status != "started":
            raise RuntimeError("executor attempt did not enter started state")
        return invocation, started

    async def _load_invocation(
        self,
        permit: DispatchPermit,
    ) -> DurableEffectInvocation:
        slot = await self._store.load_effect_slot(
            permit.session_id,
            permit.effect_id,
        )
        if slot is None:
            raise ValueError("dispatched effect slot does not exist")
        if slot.status != "dispatched":
            raise ValueError("effect slot is not dispatched")
        payload = slot.payload
        expected = (
            payload.get("attempt_id"),
            payload.get("authorization_transition_id"),
            payload.get("dispatch_owner_epoch"),
            payload.get("idempotency_key"),
        )
        actual = (
            permit.attempt_id,
            permit.authorization_transition_id,
            permit.owner_epoch,
            permit.idempotency_key,
        )
        if expected != actual:
            raise ValueError("dispatch permit does not match durable effect authority")
        effect_kind = payload.get("effect_kind")
        effect_payload = payload.get("payload")
        if not isinstance(effect_kind, str) or not effect_kind:
            raise ValueError("durable effect kind is missing")
        if not isinstance(effect_payload, Mapping):
            raise ValueError("durable effect payload is missing")
        return DurableEffectInvocation(
            session_id=permit.session_id,
            effect_id=permit.effect_id,
            attempt_id=permit.attempt_id,
            authorization_transition_id=permit.authorization_transition_id,
            owner_epoch=permit.owner_epoch,
            effect_kind=effect_kind,
            payload=effect_payload,
            idempotency_key=permit.idempotency_key,
        )

    async def _quiesce_after_claim_failure(
        self,
        authority: OwnerAuthority,
        permit: DispatchPermit,
    ) -> Exception | None:
        try:
            await self._quiesce_through_cancellation(
                authority,
                permit,
                None,
                0,
            )
        except Exception as exc:
            return exc
        return None

    async def _quiesce_through_cancellation(
        self,
        authority: OwnerAuthority,
        permit: DispatchPermit,
        invocation: DurableEffectInvocation | None,
        claim_generation: int,
    ) -> None:
        evidence_ref = (
            self._quiescence_evidence_factory(invocation, claim_generation)
            if invocation is not None
            else (
                f"executor:{self._executor_id}:{permit.effect_id}:"
                f"{permit.attempt_id}:claim:quiescent"
            )
        )
        if not isinstance(evidence_ref, str) or not evidence_ref:
            raise ValueError("quiescence evidence reference must be non-empty")
        cleanup_task = asyncio.create_task(
            self._store.quiesce_claimed_executor_attempt(
                authority,
                effect_id=permit.effect_id,
                attempt_id=permit.attempt_id,
                authorization_transition_id=permit.authorization_transition_id,
                executor_id=self._executor_id,
                now=self._clock(),
                evidence_ref=evidence_ref,
            )
        )
        quiescent, caller_cancelled = await _await_task_through_cancellation(
            cleanup_task
        )
        if quiescent.status != "quiescent":
            raise RuntimeError("executor attempt did not enter quiescent state")
        if caller_cancelled:
            raise asyncio.CancelledError


_T = TypeVar("_T")


async def _await_task_through_cancellation(
    task: asyncio.Task[_T],
) -> tuple[_T, bool]:
    caller_cancelled = False
    while True:
        try:
            return await asyncio.shield(task), caller_cancelled
        except asyncio.CancelledError:
            caller_cancelled = True
            if not task.done():
                continue
            if task.cancelled():
                raise
            return task.result(), True


def _validate_authorized_attempt(
    attempt: ExecutorAttemptRecord | None,
    permit: DispatchPermit,
) -> None:
    if attempt is None:
        raise ValueError("executor attempt does not exist")
    expected = (
        permit.session_id,
        permit.effect_id,
        permit.attempt_id,
        permit.authorization_transition_id,
        permit.owner_epoch,
        "authorized_unclaimed",
    )
    actual = (
        attempt.session_id,
        attempt.effect_id,
        attempt.attempt_id,
        attempt.authorization_transition_id,
        attempt.dispatch_owner_epoch,
        attempt.status,
    )
    if actual != expected:
        raise ValueError("executor attempt does not match claimed dispatch permit")


class LocalToolEffectBackend:
    """Adapter over the existing host-owned CoreToolExecutor."""

    __slots__ = ("_executor",)

    def __init__(self, executor: Any) -> None:
        self._executor = executor

    async def execute(
        self,
        invocation: DurableEffectInvocation,
        cancellation: CancellationToken,
    ) -> EffectExecutionResult:
        del cancellation
        tool_name = invocation.payload.get("tool_name")
        arguments = invocation.payload.get("arguments", {})
        if not isinstance(tool_name, str) or not tool_name:
            return EffectFailedResult(
                error=FailureReport(
                    code="invalid_tool_effect",
                    message="tool effect is missing tool_name",
                )
            )
        if not isinstance(arguments, Mapping):
            return EffectFailedResult(
                error=FailureReport(
                    code="invalid_tool_effect",
                    message="tool effect arguments must be an object",
                )
            )
        result = await self._executor.execute_tool_async(
            name=tool_name,
            arguments=dict(arguments),
        )
        if result is UNHANDLED_TOOL_RESULT:
            return EffectFailedResult(
                error=FailureReport(
                    code="unhandled_tool_effect",
                    message=f"host tool executor does not handle {tool_name}",
                )
            )
        return EffectCompletedResult(result=result)


class RemoteEffectBackend:
    """Adapter over an existing ExternalExecutor and authorized plan builder."""

    __slots__ = ("_executor", "_plan_builder")

    def __init__(
        self,
        executor: ExternalExecutor,
        plan_builder: Callable[
            [DurableEffectInvocation], ExecutorPlan | Awaitable[ExecutorPlan]
        ],
    ) -> None:
        self._executor = executor
        self._plan_builder = plan_builder

    async def execute(
        self,
        invocation: DurableEffectInvocation,
        cancellation: CancellationToken,
    ) -> EffectExecutionResult:
        del cancellation
        plan = self._plan_builder(invocation)
        if isinstance(plan, Awaitable):
            plan = await plan
        result: ExecutorResult = await self._executor.submit(plan)
        payload: JSONObject = {
            "status": result.status,
            "summary": result.sanitized_summary,
            "evidence": result.evidence_as_json(),
            "metadata": dict(result.metadata),
        }
        if result.status == "succeeded":
            return EffectCompletedResult(result=payload)
        return EffectFailedResult(
            error=FailureReport(
                code=result.error_type or f"external_executor_{result.status}",
                message=(
                    result.error_message
                    or result.sanitized_summary
                    or f"external executor returned {result.status}"
                ),
                details=payload,
            )
        )


def _exception_message(exc: BaseException) -> str:
    message = str(exc).strip()
    return message or type(exc).__name__ or "unknown error"
