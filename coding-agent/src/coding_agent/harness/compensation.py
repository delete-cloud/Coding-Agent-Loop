"""In-memory reference model for ADR-0076 compensation semantics."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable, TypeVar


_ATTEMPT_STATES = {"prepared", "dispatched", "unknown", "failed", "completed"}
_SETTLEMENT_STATES = {"absent", "resolved"}
_TERMINAL_ATTEMPT_STATES = {"failed", "completed"}
_ATTEMPT_TRANSITIONS = {
    "prepared": {"dispatched"},
    "dispatched": {"unknown", "failed", "completed"},
    "unknown": {"failed", "completed"},
    "failed": set(),
    "completed": set(),
}


class CompensationAdmissionError(RuntimeError):
    """Raised when the current A/B state forbids compensation admission."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ApprovalWait:
    session_id: str
    run_id: str
    approval_id: str
    effect_id: int


@dataclass(frozen=True)
class ApprovalResult:
    effect_id: int
    dispatched: bool
    dispatch_count: int
    reason: str | None = None


@dataclass(frozen=True)
class CompensationReceipt:
    generation: int
    compensation_effect_id: int
    attempt_state: str
    settlement: str

    def as_dict(self) -> dict[str, int | str]:
        return {
            "generation": self.generation,
            "compensation_effect_id": self.compensation_effect_id,
            "attempt_state": self.attempt_state,
            "settlement": self.settlement,
        }


@dataclass(frozen=True)
class EffectView:
    session_id: str
    effect_id: int
    state: str
    dispatch_count: int
    settlement: str


@dataclass(frozen=True)
class RepairResult:
    atomic_writes: tuple[tuple[str, str], ...]
    transaction_count: int


@dataclass(frozen=True)
class RaceResult:
    admission: str
    committed_writes: tuple[tuple[str, int, str], ...]


@dataclass
class _Attempt:
    original_effect_id: int
    generation: int
    state: str
    settlement: str
    session_id: str | None
    dispatch_count: int = 0


@dataclass(frozen=True)
class _ReceiptIdentity:
    generation: int
    compensation_effect_id: int


_ResultT = TypeVar("_ResultT")
_ErrorT = TypeVar("_ErrorT", bound=BaseException)


class CompensationLedger:
    """Thread-safe in-memory model of approval and compensation effects."""

    def __init__(self, *, first_effect_id: int = 1) -> None:
        if first_effect_id < 0:
            raise ValueError("first_effect_id must be non-negative")
        self.next_effect_id = first_effect_id
        self._lock = RLock()
        self._approval_waits: dict[str, ApprovalWait] = {}
        self._attempts: dict[int, _Attempt] = {}
        self._attempts_by_original: dict[int, list[int]] = {}
        self._receipts: dict[tuple[str, int, str], _ReceiptIdentity] = {}
        self._cas: dict[int, int] = {}
        self._projection_epoch = 0
        self._checkpoint_id: str | None = None

    @staticmethod
    def _session_id(fence: dict[str, Any]) -> str:
        session_id = fence.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("fence requires a non-empty session_id")
        return session_id

    def _allocate_effect_id(self) -> int:
        effect_id = self.next_effect_id
        self.next_effect_id += 1
        return effect_id

    def _latest_attempt(self, original_effect_id: int) -> tuple[int, _Attempt] | None:
        effect_ids = self._attempts_by_original.get(original_effect_id)
        if not effect_ids:
            return None
        effect_id = max(
            effect_ids,
            key=lambda candidate: self._attempts[candidate].generation,
        )
        return effect_id, self._attempts[effect_id]

    @staticmethod
    def _require_target_owner(attempt: _Attempt, session_id: str) -> None:
        if attempt.session_id != session_id:
            raise ValueError("target effect belongs to another session")

    def _bump_cas(self, original_effect_id: int) -> tuple[int, int]:
        before = self._cas.get(original_effect_id, 0)
        after = before + 1
        self._cas[original_effect_id] = after
        return before, after

    def _receipt(self, identity: _ReceiptIdentity) -> CompensationReceipt:
        attempt = self._attempts[identity.compensation_effect_id]
        return CompensationReceipt(
            generation=identity.generation,
            compensation_effect_id=identity.compensation_effect_id,
            attempt_state=attempt.state,
            settlement=attempt.settlement,
        )

    def establish_approval_wait(
        self,
        *,
        fence: dict[str, Any],
        run_id: str,
        approval_id: str,
    ) -> ApprovalWait:
        session_id = self._session_id(fence)
        with self._lock:
            existing = self._approval_waits.get(approval_id)
            if existing is not None:
                if existing.session_id != session_id:
                    raise ValueError("approval_id belongs to another session")
                return existing
            effect_id = self._allocate_effect_id()
            wait = ApprovalWait(session_id, run_id, approval_id, effect_id)
            self._approval_waits[approval_id] = wait
            self._attempts[effect_id] = _Attempt(
                original_effect_id=effect_id,
                generation=0,
                state="prepared",
                settlement="absent",
                session_id=session_id,
            )
            return wait

    def restore(self, *, checkpoint_id: str, projection_epoch: int) -> None:
        with self._lock:
            self._checkpoint_id = checkpoint_id
            self._projection_epoch = projection_epoch

    def approve(
        self,
        *,
        fence: dict[str, Any],
        approval_id: str,
    ) -> ApprovalResult:
        session_id = self._session_id(fence)
        with self._lock:
            wait = self._approval_waits[approval_id]
            if wait.session_id != session_id:
                raise ValueError("approval_id belongs to another session")
            attempt = self._attempts[wait.effect_id]
            if attempt.state in {"dispatched", "unknown"}:
                if attempt.dispatch_count == 0:
                    attempt.dispatch_count = 1
                return ApprovalResult(
                    effect_id=wait.effect_id,
                    dispatched=False,
                    dispatch_count=attempt.dispatch_count,
                    reason=f"attempt_{attempt.state}_blocks_redispatch",
                )
            if attempt.state == "prepared":
                attempt.state = "dispatched"
                attempt.dispatch_count += 1
                return ApprovalResult(
                    effect_id=wait.effect_id,
                    dispatched=True,
                    dispatch_count=attempt.dispatch_count,
                )
            return ApprovalResult(
                effect_id=wait.effect_id,
                dispatched=False,
                dispatch_count=attempt.dispatch_count,
                reason=f"attempt_{attempt.state}_blocks_redispatch",
            )

    def effect(self, session_id: str, effect_id: int) -> EffectView:
        with self._lock:
            attempt = self._attempts[effect_id]
            if attempt.session_id != session_id:
                raise KeyError((session_id, effect_id))
            return EffectView(
                session_id=session_id,
                effect_id=effect_id,
                state=attempt.state,
                dispatch_count=attempt.dispatch_count,
                settlement=attempt.settlement,
            )

    def set_attempt_state(self, *, effect_id: int, state: str) -> None:
        """Set fixture state, auto-promoting prepared attempts through dispatched."""
        if state not in _ATTEMPT_STATES:
            raise ValueError(f"invalid attempt state: {state}")
        with self._lock:
            attempt = self._attempts[effect_id]
            if attempt.state == "prepared" and state in {"unknown", "failed", "completed"}:
                attempt.state = "dispatched"
                attempt.dispatch_count = 1
            if state not in _ATTEMPT_TRANSITIONS[attempt.state]:
                raise ValueError(
                    f"illegal attempt transition: {attempt.state} -> {state}"
                )
            if state == "dispatched" and attempt.dispatch_count == 0:
                attempt.dispatch_count = 1
            attempt.state = state

    def seed_attempt(
        self,
        *,
        original_effect_id: int,
        compensation_effect_id: int,
        generation: int,
        state: str,
        settlement: str,
        session_id: str,
    ) -> None:
        if state not in _ATTEMPT_STATES:
            raise ValueError(f"invalid attempt state: {state}")
        if settlement not in _SETTLEMENT_STATES:
            raise ValueError(f"invalid settlement state: {settlement}")
        if generation < 0:
            raise ValueError("generation must be non-negative")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        with self._lock:
            if compensation_effect_id in self._attempts:
                raise ValueError("effect_id already exists")
            if any(
                self._attempts[effect_id].generation == generation
                for effect_id in self._attempts_by_original.get(original_effect_id, ())
            ):
                raise ValueError("generation already exists")
            self._attempts[compensation_effect_id] = _Attempt(
                original_effect_id=original_effect_id,
                generation=generation,
                state=state,
                settlement=settlement,
                session_id=session_id,
                dispatch_count=int(state != "prepared"),
            )
            self._attempts_by_original.setdefault(original_effect_id, []).append(
                compensation_effect_id
            )
            self.next_effect_id = max(self.next_effect_id, compensation_effect_id + 1)

    def classify(self, *, original_effect_id: int) -> str:
        with self._lock:
            latest = self._latest_attempt(original_effect_id)
            if latest is None:
                return "admissible"
            _, attempt = latest
            if attempt.settlement == "resolved":
                return "already_resolved"
            if attempt.state == "completed":
                return "repair_only"
            return "admissible"

    def compensate(
        self,
        *,
        fence: dict[str, Any],
        original_effect_id: int,
        client_key: str,
        generation: int,
    ) -> CompensationReceipt:
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            raise CompensationAdmissionError("invalid_generation")
        session_id = self._session_id(fence)
        receipt_key = (session_id, original_effect_id, client_key)
        with self._lock:
            existing = self._receipts.get(receipt_key)
            if existing is not None:
                return self._receipt(existing)
            latest = self._latest_attempt(original_effect_id)
            if latest is not None:
                self._require_target_owner(latest[1], session_id)
            classification = self.classify(original_effect_id=original_effect_id)
            if classification != "admissible":
                raise CompensationAdmissionError(classification)
            if latest is not None and generation != latest[1].generation + 1:
                raise CompensationAdmissionError("invalid_generation")
            effect_id = self._allocate_effect_id()
            identity = _ReceiptIdentity(generation, effect_id)
            self._attempts[effect_id] = _Attempt(
                original_effect_id=original_effect_id,
                generation=generation,
                state="prepared",
                settlement="absent",
                session_id=session_id,
            )
            self._attempts_by_original.setdefault(original_effect_id, []).append(effect_id)
            self._receipts[receipt_key] = identity
            self._bump_cas(original_effect_id)
            return self._receipt(identity)

    def resolve_settlement(self, *, effect_id: int) -> None:
        with self._lock:
            attempt = self._attempts[effect_id]
            if attempt.state != "completed":
                raise ValueError("settlement requires a completed attempt")
            attempt.settlement = "resolved"

    def quiescent(self, *, original_effect_id: int) -> bool:
        with self._lock:
            return not any(
                self._attempts[effect_id].settlement == "resolved"
                for effect_id in self._attempts_by_original.get(original_effect_id, ())
            )

    def attempt_ids(self, *, original_effect_id: int) -> tuple[int, ...]:
        with self._lock:
            return tuple(self._attempts_by_original.get(original_effect_id, ()))

    def compensation_cas(self, *, original_effect_id: int) -> int:
        with self._lock:
            return self._cas.get(original_effect_id, 0)

    def repair(
        self,
        *,
        fence: dict[str, Any],
        original_effect_id: int,
        observation: str,
    ) -> RepairResult:
        session_id = self._session_id(fence)
        if observation not in {"unobserved", "observed_failed", "observed_completed"}:
            raise ValueError(f"invalid repair observation: {observation}")
        with self._lock:
            latest = self._latest_attempt(original_effect_id)
            if latest is None:
                raise KeyError(original_effect_id)
            _, attempt = latest
            self._require_target_owner(attempt, session_id)
            writes: list[tuple[str, str]] = []
            if attempt.settlement != "resolved" and observation == "observed_failed":
                if attempt.state not in _TERMINAL_ATTEMPT_STATES:
                    attempt.state = "failed"
                    writes.append(("attempt_a", "failed"))
            elif attempt.settlement != "resolved" and observation == "observed_completed":
                if attempt.state == "failed":
                    raise ValueError("cannot repair failed attempt as completed")
                if attempt.state != "completed":
                    attempt.state = "completed"
                    writes.append(("attempt_a", "completed"))
                attempt.settlement = "resolved"
                writes.append(("settlement_b", "resolved"))
            if writes:
                self._bump_cas(original_effect_id)
            return RepairResult(tuple(writes), int(bool(writes)))

    def race_repair_and_admission(
        self,
        *,
        fence: dict[str, Any],
        original_effect_id: int,
        new_client_key: str,
        new_generation: int,
    ) -> RaceResult:
        with self._lock:
            latest = self._latest_attempt(original_effect_id)
            if latest is None:
                raise KeyError(original_effect_id)
            effect_id, attempt = latest
            if attempt.state != "completed" or attempt.settlement != "absent":
                raise ValueError("race requires completed A with absent B")
            try:
                self.compensate(
                    fence=fence,
                    original_effect_id=original_effect_id,
                    client_key=new_client_key,
                    generation=new_generation,
                )
            except CompensationAdmissionError as error:
                if error.code != "repair_only":
                    raise
            else:
                raise AssertionError("repair-only admission unexpectedly succeeded")
            result = self.repair(
                fence=fence,
                original_effect_id=original_effect_id,
                observation="observed_completed",
            )
            return RaceResult(
                admission="rejected_repair_only",
                committed_writes=tuple(
                    (record, effect_id, state) for record, state in result.atomic_writes
                ),
            )

    def run_serialized_operations(
        self,
        *,
        fence: dict[str, Any],
        original_effect_id: int,
        operations: tuple[str, ...],
    ) -> tuple[tuple[str, int, int, str], ...]:
        valid_operations = {"admission_c1", "repair", "admission_c2"}
        invalid = next(
            (operation for operation in operations if operation not in valid_operations),
            None,
        )
        if invalid is not None:
            raise ValueError(f"invalid serialized operation: {invalid}")
        trace: list[tuple[str, int, int, str]] = []
        with self._lock:
            for operation in operations:
                before = self.compensation_cas(original_effect_id=original_effect_id)
                if operation.startswith("admission_"):
                    latest = self._latest_attempt(original_effect_id)
                    generation = 1 if latest is None else latest[1].generation + 1
                    self.compensate(
                        fence=fence,
                        original_effect_id=original_effect_id,
                        client_key=f"serialized:{operation}",
                        generation=generation,
                    )
                    mutated = self.compensation_cas(
                        original_effect_id=original_effect_id
                    ) != before
                    outcome = "admitted" if mutated else "replayed"
                else:
                    latest = self._latest_attempt(original_effect_id)
                    if latest is None:
                        raise KeyError(original_effect_id)
                    if latest[1].state == "prepared":
                        self.set_attempt_state(effect_id=latest[0], state="dispatched")
                    self.repair(
                        fence=fence,
                        original_effect_id=original_effect_id,
                        observation="observed_failed",
                    )
                    outcome = "classified"
                after = self.compensation_cas(original_effect_id=original_effect_id)
                trace.append((operation, before, after, outcome))
        return tuple(trace)

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
