"""Authoritative harness unit-of-work commit."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime

from agentkit.runtime.contracts import (
    ApprovalSettlement,
    CommitRef,
    CommittedFactNotice,
    EffectStatus,
    OperationStateVersion,
    RuntimeCommand,
    TransitionReceipt,
)

from coding_agent.events.connected_chat import (
    ChatCommandAdmission,
    ChatCommandConflictError,
    ResumeSourceUnsettledError,
    RootRunAlreadySettledError,
    TurnInProgressError,
    build_chat_admission,
    build_root_settlement,
)
from coding_agent.runtime_activation import (
    assert_effect_status_allowed,
    parse_runtime_version,
    stamp_session_payload_for_save,
)
from coding_agent.stores.runtime_store import (
    AuthoritativeCommit,
    AuthoritativeUnitOfWork,
    CommandMailboxEntry,
    ChildBindingConflictError,
    ChildExecutionBinding,
    CommandDispositionConflictError,
    RuntimeCommandAdmission,
    RuntimeCommandAdmissionConflictError,
    EventRecord,
    EffectLedgerSlot,
    EffectMutationConflictError,
    EffectReconciliationEvidence,
    ExecutorAttemptConflictError,
    ExecutorAttemptRecord,
    JSONObject,
    RawCursor,
    RecoveredChildExecutionLease,
    RecoveredChildControlState,
    RecoveryLeaseConflictError,
    RecoveryEvidenceConflictError,
    _agent_run_sqlite_values,
    _datetime_to_json,
    _json_object_from_sql,
    _json_to_sql,
    effect_status_may_replace,
    legacy_settled_slot_may_replace,
    format_u64,
    runtime_command_from_mailbox_payload,
    runtime_command_invalidates_dispatch,
    runtime_command_targets,
    runtime_command_mailbox_payload,
    runtime_command_mailbox_payloads_equal,
    validate_new_runtime_command_target,
    validate_recovery_approval_refresh,
    adopt_parent_settlement_receipt,
    recovered_child_lease_from_payload,
    recovered_child_lease_payload,
    parse_u64,
    StateVersionConflictError,
    StaleMailboxCutError,
    StaleRecoveryGuardError,
    _sqlite_required_int,
    _sqlite_required_str,
    TransitionFingerprintMismatchError,
    _operation_state_from_sqlite_row,
    _plain_json,
    _transition_receipt_from_sqlite_row,
    effect_slot_from_mutation,
    child_execution_binding_from_payload,
    child_execution_binding_payload,
    assert_recovery_guard_shape,
    mailbox_slot_from_disposition,
    transition_commit_from_payload,
    transition_commit_payload,
    transition_mutation_fingerprint,
    state_value_with_reconciled_effect,
    snapshot_transition_unit,
    receipt_generation_may_replace,
)
from coding_agent.server.stores.session_owner_store import (
    OwnerAuthority,
    SessionOwnershipConflictError,
)
from coding_agent.stores.local_durable.fact_source_rows import (
    _event_record_from_sqlite_row,
)
from coding_agent.stores.local_durable.helpers import (
    _require_json_object,
    _require_non_empty,
)


class LocalUnitOfWorkMixin:
    async def record_effect_reconciliation_evidence(
        self,
        authority: OwnerAuthority,
        evidence: EffectReconciliationEvidence,
    ) -> EffectReconciliationEvidence:
        if evidence.session_id != authority.session_id:
            raise SessionOwnershipConflictError(
                "reconciliation evidence belongs to another session"
            )
        if evidence.reconciliation_owner_epoch != authority.epoch:
            raise SessionOwnershipConflictError(
                "reconciliation evidence owner epoch does not match authority"
            )
        payload = evidence.payload()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            row = connection.execute(
                """
                SELECT payload
                FROM session_effect_reconciliation_evidence
                WHERE session_id = ? AND evidence_ref = ?
                """,
                (authority.session_id, evidence.evidence_ref),
            ).fetchone()
            if row is not None:
                stored = _json_object_from_sql(
                    row["payload"],
                    context="effect reconciliation evidence",
                )
                if stored != payload:
                    raise RecoveryEvidenceConflictError(
                        "reconciliation evidence identity changed content"
                    )
                return evidence
            identity_row = connection.execute(
                """
                SELECT evidence_ref, payload
                FROM session_effect_reconciliation_evidence
                WHERE session_id = ? AND effect_id = ? AND attempt_id = ?
                  AND authorization_transition_id = ?
                """,
                (
                    authority.session_id,
                    evidence.effect_id,
                    evidence.attempt_id,
                    evidence.authorization_transition_id,
                ),
            ).fetchone()
            if identity_row is not None:
                raise RecoveryEvidenceConflictError(
                    "reconciliation identity already has different evidence"
                )
            connection.execute(
                """
                INSERT INTO session_effect_reconciliation_evidence (
                    session_id, evidence_ref, effect_id, attempt_id,
                    authorization_transition_id, reconciliation_owner_epoch,
                    payload
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence.session_id,
                    evidence.evidence_ref,
                    evidence.effect_id,
                    evidence.attempt_id,
                    evidence.authorization_transition_id,
                    evidence.reconciliation_owner_epoch,
                    _json_to_sql(payload),
                ),
            )
        return evidence

    async def load_effect_reconciliation_evidence(
        self,
        session_id: str,
        evidence_ref: str,
    ) -> EffectReconciliationEvidence | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload
                FROM session_effect_reconciliation_evidence
                WHERE session_id = ? AND evidence_ref = ?
                """,
                (session_id, evidence_ref),
            ).fetchone()
        if row is None:
            return None
        return _reconciliation_evidence_from_payload(
            _json_object_from_sql(
                row["payload"],
                context="effect reconciliation evidence",
            )
        )

    async def load_executor_attempt(
        self,
        session_id: str,
        effect_id: str,
        attempt_id: str,
        authorization_transition_id: str,
    ) -> ExecutorAttemptRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload FROM session_executor_attempts
                WHERE session_id = ? AND effect_id = ? AND attempt_id = ?
                  AND authorization_transition_id = ?
                """,
                (
                    session_id,
                    effect_id,
                    attempt_id,
                    authorization_transition_id,
                ),
            ).fetchone()
        if row is None:
            return None
        return _executor_attempt_from_payload(
            _json_object_from_sql(
                row["payload"],
                context="executor attempt",
            )
        )

    async def reserve_executor_attempt(
        self,
        authority: OwnerAuthority,
        *,
        effect_id: str,
        attempt_id: str,
        authorization_transition_id: str,
        executor_id: str,
        lease_expires_at: datetime,
    ) -> ExecutorAttemptRecord:
        key = (
            authority.session_id,
            effect_id,
            attempt_id,
            authorization_transition_id,
        )
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            row = connection.execute(
                """
                SELECT payload FROM session_executor_attempts
                WHERE session_id = ? AND effect_id = ? AND attempt_id = ?
                  AND authorization_transition_id = ?
                """,
                key,
            ).fetchone()
            if row is None:
                raise ExecutorAttemptConflictError("executor attempt does not exist")
            current = _executor_attempt_from_payload(
                _json_object_from_sql(row["payload"], context="executor attempt")
            )
            if current.status == "reserved":
                if (
                    current.executor_id == executor_id
                    and current.reservation_lease_expires_at == lease_expires_at
                ):
                    return current
                raise ExecutorAttemptConflictError(
                    "executor reservation replay changed content"
                )
            reserved = replace(
                current,
                status="reserved",
                executor_id=executor_id,
                claim_generation=current.claim_generation + 1,
                reservation_lease_expires_at=lease_expires_at,
            )
            if (
                current.status != "authorized_unclaimed"
                or current.dispatch_owner_epoch != authority.epoch
            ):
                raise ExecutorAttemptConflictError(
                    "executor attempt cannot be reserved"
                )
            connection.execute(
                """
                UPDATE session_executor_attempts SET status = ?, payload = ?
                WHERE session_id = ? AND effect_id = ? AND attempt_id = ?
                  AND authorization_transition_id = ?
                """,
                ("reserved", _json_to_sql(reserved.payload()), *key),
            )
        return reserved

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
    ) -> ExecutorAttemptRecord:
        return self._transition_executor_attempt(
            authority,
            effect_id=effect_id,
            attempt_id=attempt_id,
            authorization_transition_id=authorization_transition_id,
            executor_id=executor_id,
            claim_generation=claim_generation,
            now=now,
            target_status="started",
            evidence_ref=None,
        )

    async def mark_executor_attempt_quiescent(
        self,
        authority: OwnerAuthority,
        *,
        effect_id: str,
        attempt_id: str,
        authorization_transition_id: str,
        executor_id: str,
        claim_generation: int,
        now: datetime,
        evidence_ref: str,
    ) -> ExecutorAttemptRecord:
        return self._transition_executor_attempt(
            authority,
            effect_id=effect_id,
            attempt_id=attempt_id,
            authorization_transition_id=authorization_transition_id,
            executor_id=executor_id,
            claim_generation=claim_generation,
            now=now,
            target_status="quiescent",
            evidence_ref=evidence_ref,
        )

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
    ) -> ExecutorAttemptRecord:
        key = (
            authority.session_id,
            effect_id,
            attempt_id,
            authorization_transition_id,
        )
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            row = connection.execute(
                """
                SELECT payload FROM session_executor_attempts
                WHERE session_id = ? AND effect_id = ? AND attempt_id = ?
                  AND authorization_transition_id = ?
                """,
                key,
            ).fetchone()
            if row is None:
                raise ExecutorAttemptConflictError("executor attempt does not exist")
            current = _executor_attempt_from_payload(
                _json_object_from_sql(row["payload"], context="executor attempt")
            )
            if current.status == "quiescent":
                if (
                    current.executor_id == executor_id
                    and current.quiescence_evidence_ref == evidence_ref
                ):
                    return current
                raise ExecutorAttemptConflictError(
                    "executor quiescence replay changed content"
                )
            if current.status not in {
                "authorized_unclaimed",
                "reserved",
                "started",
            }:
                raise ExecutorAttemptConflictError(
                    "executor attempt cannot be quiesced"
                )
            if current.dispatch_owner_epoch > authority.epoch:
                raise ExecutorAttemptConflictError(
                    "executor attempt belongs to a newer owner"
                )
            if current.status != "authorized_unclaimed" and (
                current.executor_id != executor_id
            ):
                raise ExecutorAttemptConflictError("executor claim identity mismatch")
            updated = replace(
                current,
                status="quiescent",
                executor_id=executor_id,
                claim_generation=max(1, current.claim_generation),
                reservation_lease_expires_at=(
                    now
                    if current.reservation_lease_expires_at is None
                    else current.reservation_lease_expires_at
                ),
                quiescence_evidence_ref=evidence_ref,
            )
            connection.execute(
                """
                UPDATE session_executor_attempts SET status = ?, payload = ?
                WHERE session_id = ? AND effect_id = ? AND attempt_id = ?
                  AND authorization_transition_id = ?
                """,
                ("quiescent", _json_to_sql(updated.payload()), *key),
            )
        return updated

    async def revoke_expired_executor_reservation(
        self,
        authority: OwnerAuthority,
        *,
        effect_id: str,
        attempt_id: str,
        authorization_transition_id: str,
        executor_id: str,
        claim_generation: int,
        now: datetime,
        evidence_ref: str,
    ) -> ExecutorAttemptRecord:
        return self._transition_executor_attempt(
            authority,
            effect_id=effect_id,
            attempt_id=attempt_id,
            authorization_transition_id=authorization_transition_id,
            executor_id=executor_id,
            claim_generation=claim_generation,
            now=now,
            target_status="quiescent",
            evidence_ref=evidence_ref,
            require_fenced_expired_reservation=True,
        )

    def _transition_executor_attempt(
        self,
        authority: OwnerAuthority,
        *,
        effect_id: str,
        attempt_id: str,
        authorization_transition_id: str,
        executor_id: str,
        claim_generation: int,
        now: datetime,
        target_status: str,
        evidence_ref: str | None,
        require_fenced_expired_reservation: bool = False,
    ) -> ExecutorAttemptRecord:
        key = (
            authority.session_id,
            effect_id,
            attempt_id,
            authorization_transition_id,
        )
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            row = connection.execute(
                """
                SELECT payload FROM session_executor_attempts
                WHERE session_id = ? AND effect_id = ? AND attempt_id = ?
                  AND authorization_transition_id = ?
                """,
                key,
            ).fetchone()
            if row is None:
                raise ExecutorAttemptConflictError("executor attempt does not exist")
            current = _executor_attempt_from_payload(
                _json_object_from_sql(row["payload"], context="executor attempt")
            )
            if current.status == target_status:
                if (
                    current.executor_id == executor_id
                    and current.claim_generation == claim_generation
                    and current.quiescence_evidence_ref == evidence_ref
                ):
                    return current
                raise ExecutorAttemptConflictError(
                    "executor transition replay changed content"
                )
            if (
                current.executor_id != executor_id
                or current.claim_generation != claim_generation
            ):
                raise ExecutorAttemptConflictError("executor claim identity mismatch")
            if require_fenced_expired_reservation:
                if (
                    current.status != "reserved"
                    or current.reservation_lease_expires_at is None
                    or current.reservation_lease_expires_at > now
                    or current.dispatch_owner_epoch >= authority.epoch
                ):
                    raise ExecutorAttemptConflictError(
                        "reservation is not both expired and owner-fenced"
                    )
            else:
                expected_status = (
                    "reserved" if target_status == "started" else "started"
                )
                owner_transition_allowed = (
                    current.dispatch_owner_epoch == authority.epoch
                    if target_status == "started"
                    else current.dispatch_owner_epoch <= authority.epoch
                )
                if current.status != expected_status or not owner_transition_allowed:
                    raise ExecutorAttemptConflictError(
                        "executor attempt transition is not authorized"
                    )
                if (
                    target_status == "started"
                    and current.reservation_lease_expires_at is not None
                    and current.reservation_lease_expires_at <= now
                ):
                    raise ExecutorAttemptConflictError(
                        "executor reservation has expired"
                    )
            updated = replace(
                current,
                status=target_status,
                quiescence_evidence_ref=evidence_ref,
            )
            connection.execute(
                """
                UPDATE session_executor_attempts SET status = ?, payload = ?
                WHERE session_id = ? AND effect_id = ? AND attempt_id = ?
                  AND authorization_transition_id = ?
                """,
                (target_status, _json_to_sql(updated.payload()), *key),
            )
        return updated

    async def load_child_execution_binding(
        self,
        session_id: str,
        *,
        parent_effect_id: str | None = None,
        child_run_id: str | None = None,
    ) -> ChildExecutionBinding | None:
        if (parent_effect_id is None) == (child_run_id is None):
            raise ValueError("exactly one child binding identity must be provided")
        column = "parent_effect_id" if parent_effect_id is not None else "child_run_id"
        identity = parent_effect_id if parent_effect_id is not None else child_run_id
        with self._lock, self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT payload FROM session_child_bindings
                WHERE session_id = ? AND {column} = ?
                """,
                (session_id, identity),
            ).fetchone()
        if row is None:
            return None
        return child_execution_binding_from_payload(
            _json_object_from_sql(row["payload"], context="child binding")
        )

    async def acquire_recovered_child_execution_lease(
        self,
        authority: OwnerAuthority,
        *,
        child_run_id: str,
        lease_id: str,
    ) -> RecoveredChildExecutionLease:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            fact_source = self._ensure_fact_source(
                connection,
                authority.session_id,
            )
            binding_row = connection.execute(
                """
                SELECT payload FROM session_child_bindings
                WHERE session_id = ? AND child_run_id = ?
                """,
                (authority.session_id, child_run_id),
            ).fetchone()
            if binding_row is None:
                raise RecoveryLeaseConflictError("child binding does not exist")
            binding = child_execution_binding_from_payload(
                _json_object_from_sql(
                    binding_row["payload"],
                    context="child binding",
                )
            )
            ledger_row = connection.execute(
                """
                SELECT child_run_id, payload FROM session_recovery_leases
                WHERE session_id = ? AND lease_id = ?
                """,
                (authority.session_id, lease_id),
            ).fetchone()
            if ledger_row is not None:
                stored_lease = recovered_child_lease_from_payload(
                    _json_object_from_sql(
                        ledger_row["payload"],
                        context="recovery lease ledger",
                    )
                )
                current_snapshot = self._matched_child_mailbox_snapshot(
                    connection,
                    binding,
                    prior_session_seq=stored_lease.prior_session_seq,
                    resume_session_seq=format_u64(fact_source.session_seq_int),
                )
                self._assert_no_pending_child_control(current_snapshot)
                if (
                    ledger_row["child_run_id"] != child_run_id
                    or binding.active_lease != stored_lease
                    or stored_lease.owner_epoch != authority.epoch
                ):
                    raise RecoveryLeaseConflictError(
                        "recovery lease identity was already issued"
                    )
                return stored_lease
            active_lease = binding.active_lease
            if (
                active_lease is not None
                and active_lease.lease_id != lease_id
                and active_lease.owner_epoch >= authority.epoch
            ):
                raise RecoveryLeaseConflictError(
                    "another active recovery lease owns this child"
                )
            effect_row = connection.execute(
                """
                SELECT status, payload FROM session_effect_slots
                WHERE session_id = ? AND effect_id = ?
                """,
                (authority.session_id, binding.parent_effect_id),
            ).fetchone()
            if effect_row is None or effect_row["status"] != "dispatched":
                raise RecoveryLeaseConflictError(
                    "parent effect is not retained as dispatched"
                )
            effect_payload = _json_object_from_sql(
                effect_row["payload"],
                context="parent effect",
            )
            if (
                effect_payload.get("attempt_id") != binding.parent_attempt_id
                or effect_payload.get("authorization_transition_id")
                != binding.authorization_transition_id
            ):
                raise RecoveryLeaseConflictError(
                    "parent dispatch authorization does not match child binding"
                )
            attempt_row = connection.execute(
                """
                SELECT payload FROM session_executor_attempts
                WHERE session_id = ? AND effect_id = ? AND attempt_id = ?
                  AND authorization_transition_id = ?
                """,
                (
                    authority.session_id,
                    binding.parent_effect_id,
                    binding.parent_attempt_id,
                    binding.authorization_transition_id,
                ),
            ).fetchone()
            if attempt_row is None:
                raise RecoveryLeaseConflictError(
                    "parent executor attempt does not exist"
                )
            attempt = _executor_attempt_from_payload(
                _json_object_from_sql(
                    attempt_row["payload"],
                    context="parent executor attempt",
                )
            )
            if (
                attempt.status != "quiescent"
                or attempt.dispatch_owner_epoch >= authority.epoch
            ):
                raise RecoveryLeaseConflictError(
                    "parent executor attempt is not quiescent under an older owner"
                )
            prior_session_seq = attempt.authorization_mailbox_session_seq
            resume_session_seq = format_u64(fact_source.session_seq_int)
            mailbox_snapshot = self._matched_child_mailbox_snapshot(
                connection,
                binding,
                prior_session_seq=prior_session_seq,
                resume_session_seq=resume_session_seq,
            )
            self._assert_no_pending_child_control(mailbox_snapshot)
            lease = RecoveredChildExecutionLease(
                session_id=authority.session_id,
                child_run_id=binding.child_run_id,
                lease_id=lease_id,
                resume_generation=(
                    1 if active_lease is None else active_lease.resume_generation + 1
                ),
                resume_cut=format_u64(fact_source.dispatch_generation_int),
                owner_epoch=authority.epoch,
                prior_session_seq=prior_session_seq,
                resume_session_seq=resume_session_seq,
                mailbox_snapshot=mailbox_snapshot,
            )
            if active_lease is not None:
                connection.execute(
                    """
                    UPDATE session_recovery_leases SET status = 'superseded'
                    WHERE session_id = ? AND lease_id = ?
                    """,
                    (authority.session_id, active_lease.lease_id),
                )
            updated = replace(binding, active_lease=lease)
            connection.execute(
                """
                UPDATE session_child_bindings SET payload = ?
                WHERE session_id = ? AND child_run_id = ?
                """,
                (
                    _json_to_sql(child_execution_binding_payload(updated)),
                    authority.session_id,
                    child_run_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO session_recovery_leases (
                    session_id, lease_id, child_run_id, status, payload
                )
                VALUES (?, ?, ?, 'active', ?)
                """,
                (
                    authority.session_id,
                    lease.lease_id,
                    lease.child_run_id,
                    _json_to_sql(recovered_child_lease_payload(lease)),
                ),
            )
        return lease

    async def rebase_recovered_child_execution_lease(
        self,
        authority: OwnerAuthority,
        *,
        lease: RecoveredChildExecutionLease,
    ) -> RecoveredChildExecutionLease:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            fact_source = self._ensure_fact_source(
                connection,
                authority.session_id,
            )
            binding_row = connection.execute(
                """
                SELECT payload FROM session_child_bindings
                WHERE session_id = ? AND child_run_id = ?
                """,
                (authority.session_id, lease.child_run_id),
            ).fetchone()
            if binding_row is None:
                raise RecoveryLeaseConflictError("child binding does not exist")
            binding = child_execution_binding_from_payload(
                _json_object_from_sql(
                    binding_row["payload"],
                    context="child binding",
                )
            )
            if binding.active_lease != lease:
                raise RecoveryLeaseConflictError(
                    "recovery lease identity or generation is stale"
                )
            effect_row = connection.execute(
                """
                SELECT status, payload FROM session_effect_slots
                WHERE session_id = ? AND effect_id = ?
                """,
                (authority.session_id, binding.parent_effect_id),
            ).fetchone()
            if effect_row is None or effect_row["status"] != "dispatched":
                raise RecoveryLeaseConflictError(
                    "parent effect is no longer dispatched"
                )
            effect_payload = _json_object_from_sql(
                effect_row["payload"],
                context="parent effect",
            )
            if (
                effect_payload.get("authorization_transition_id")
                != binding.authorization_transition_id
            ):
                raise RecoveryLeaseConflictError(
                    "parent dispatch authorization changed"
                )
            desired_session_seq = format_u64(fact_source.session_seq_int)
            mailbox_snapshot = self._matched_child_mailbox_snapshot(
                connection,
                binding,
                prior_session_seq=lease.prior_session_seq,
                resume_session_seq=desired_session_seq,
            )
            self._assert_no_pending_child_control(mailbox_snapshot)
            if any(
                entry.command.command_kind == "approval_decision"
                for entry in mailbox_snapshot
            ):
                raise RecoveryLeaseConflictError(
                    "approval commands require validated recovery refresh"
                )
            desired_cut = format_u64(fact_source.dispatch_generation_int)
            if (
                lease.resume_cut == desired_cut
                and lease.resume_session_seq == desired_session_seq
                and lease.mailbox_snapshot == mailbox_snapshot
            ):
                return lease
            updated_lease = replace(
                lease,
                resume_generation=lease.resume_generation + 1,
                resume_cut=desired_cut,
                resume_session_seq=desired_session_seq,
                mailbox_snapshot=mailbox_snapshot,
            )
            connection.execute(
                """
                UPDATE session_child_bindings SET payload = ?
                WHERE session_id = ? AND child_run_id = ?
                """,
                (
                    _json_to_sql(
                        child_execution_binding_payload(
                            replace(binding, active_lease=updated_lease)
                        )
                    ),
                    authority.session_id,
                    lease.child_run_id,
                ),
            )
            connection.execute(
                """
                UPDATE session_recovery_leases SET payload = ?
                WHERE session_id = ? AND lease_id = ? AND status = 'active'
                """,
                (
                    _json_to_sql(recovered_child_lease_payload(updated_lease)),
                    authority.session_id,
                    updated_lease.lease_id,
                ),
            )
        return updated_lease

    async def refresh_recovered_child_execution_lease_for_approval(
        self,
        authority: OwnerAuthority,
        *,
        lease: RecoveredChildExecutionLease,
        state_version: OperationStateVersion,
        approval: ApprovalSettlement,
        expected_dispatch_cut: str,
    ) -> RecoveredChildExecutionLease:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            fact_source = self._ensure_fact_source(connection, authority.session_id)
            current_cut = format_u64(fact_source.dispatch_generation_int)
            if current_cut != expected_dispatch_cut:
                raise RecoveryLeaseConflictError(
                    "approval refresh dispatch cut is stale"
                )
            binding_row = connection.execute(
                """
                SELECT payload FROM session_child_bindings
                WHERE session_id = ? AND child_run_id = ?
                """,
                (authority.session_id, lease.child_run_id),
            ).fetchone()
            if binding_row is None:
                raise RecoveryLeaseConflictError("child binding does not exist")
            binding = child_execution_binding_from_payload(
                _json_object_from_sql(
                    binding_row["payload"],
                    context="child binding",
                )
            )
            if binding.active_lease != lease:
                raise RecoveryLeaseConflictError(
                    "recovery lease identity or generation is stale"
                )
            state_row = connection.execute(
                """
                SELECT * FROM session_operation_states
                WHERE session_id = ? AND run_id = ?
                """,
                (authority.session_id, lease.child_run_id),
            ).fetchone()
            if state_row is None:
                raise RecoveryLeaseConflictError(
                    "recovered child operation state does not exist"
                )
            durable_state = _operation_state_from_sqlite_row(state_row)
            if durable_state != state_version:
                raise RecoveryLeaseConflictError(
                    "approval refresh child state is stale"
                )
            desired_session_seq = format_u64(fact_source.session_seq_int)
            mailbox_snapshot = self._matched_child_mailbox_snapshot(
                connection,
                binding,
                prior_session_seq=lease.prior_session_seq,
                resume_session_seq=desired_session_seq,
            )
            self._assert_no_pending_child_control(mailbox_snapshot)
            validate_recovery_approval_refresh(
                lease=lease,
                state_version=durable_state,
                approval=approval,
                mailbox_snapshot=mailbox_snapshot,
            )
            if current_cut != lease.resume_cut:
                raise RecoveryLeaseConflictError(
                    "approval refresh must keep resume_cut unchanged"
                )
            if (
                lease.resume_cut == current_cut
                and lease.resume_session_seq == desired_session_seq
                and lease.mailbox_snapshot == mailbox_snapshot
            ):
                return lease
            updated_lease = replace(
                lease,
                resume_generation=lease.resume_generation + 1,
                resume_session_seq=desired_session_seq,
                mailbox_snapshot=mailbox_snapshot,
            )
            connection.execute(
                """
                UPDATE session_child_bindings SET payload = ?
                WHERE session_id = ? AND child_run_id = ?
                """,
                (
                    _json_to_sql(
                        child_execution_binding_payload(
                            replace(binding, active_lease=updated_lease)
                        )
                    ),
                    authority.session_id,
                    lease.child_run_id,
                ),
            )
            connection.execute(
                """
                UPDATE session_recovery_leases SET payload = ?
                WHERE session_id = ? AND lease_id = ? AND status = 'active'
                """,
                (
                    _json_to_sql(recovered_child_lease_payload(updated_lease)),
                    authority.session_id,
                    updated_lease.lease_id,
                ),
            )
        return updated_lease

    async def load_recovered_child_control_state(
        self,
        authority: OwnerAuthority,
        *,
        lease: RecoveredChildExecutionLease,
    ) -> RecoveredChildControlState:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            fact_source = self._ensure_fact_source(
                connection,
                authority.session_id,
            )
            binding_row = connection.execute(
                """
                SELECT payload FROM session_child_bindings
                WHERE session_id = ? AND child_run_id = ?
                """,
                (authority.session_id, lease.child_run_id),
            ).fetchone()
            if binding_row is None:
                raise RecoveryLeaseConflictError("child binding does not exist")
            binding = child_execution_binding_from_payload(
                _json_object_from_sql(
                    binding_row["payload"],
                    context="child binding",
                )
            )
            if binding.active_lease != lease:
                raise RecoveryLeaseConflictError(
                    "recovery lease identity or generation is stale"
                )
            session_seq = format_u64(fact_source.session_seq_int)
            return RecoveredChildControlState(
                dispatch_generation=format_u64(fact_source.dispatch_generation_int),
                session_seq=session_seq,
                mailbox_snapshot=self._matched_child_mailbox_snapshot(
                    connection,
                    binding,
                    prior_session_seq=lease.prior_session_seq,
                    resume_session_seq=session_seq,
                ),
            )

    async def load_live_child_control_state(
        self,
        authority: OwnerAuthority,
        *,
        child_run_id: str,
        after_session_seq: str,
    ) -> RecoveredChildControlState:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            fact_source = self._ensure_fact_source(
                connection,
                authority.session_id,
            )
            binding_row = connection.execute(
                """
                SELECT payload FROM session_child_bindings
                WHERE session_id = ? AND child_run_id = ?
                """,
                (authority.session_id, child_run_id),
            ).fetchone()
            if binding_row is None:
                raise RecoveryLeaseConflictError("child binding does not exist")
            binding = child_execution_binding_from_payload(
                _json_object_from_sql(
                    binding_row["payload"],
                    context="child binding",
                )
            )
            session_seq = format_u64(fact_source.session_seq_int)
            return RecoveredChildControlState(
                dispatch_generation=format_u64(fact_source.dispatch_generation_int),
                session_seq=session_seq,
                mailbox_snapshot=self._matched_child_mailbox_snapshot(
                    connection,
                    binding,
                    prior_session_seq=after_session_seq,
                    resume_session_seq=session_seq,
                ),
            )

    @staticmethod
    def _matched_child_mailbox_snapshot(
        connection: sqlite3.Connection,
        binding: ChildExecutionBinding,
        *,
        prior_session_seq: str,
        resume_session_seq: str,
    ) -> tuple[CommandMailboxEntry, ...]:
        prior = parse_u64(prior_session_seq, field_name="prior_session_seq")
        resume = parse_u64(resume_session_seq, field_name="resume_session_seq")
        rows = connection.execute(
            """
            SELECT slot_id, disposition, admitted_session_seq,
                   admitted_dispatch_generation, payload
            FROM session_mailbox_slots
            WHERE session_id = ?
              AND disposition IN ('pending', 'admitted')
              AND admitted_session_seq > ?
              AND admitted_session_seq <= ?
            ORDER BY admitted_session_seq, slot_id
            """,
            (binding.session_id, prior, resume),
        ).fetchall()
        entries: list[CommandMailboxEntry] = []
        run_ids = frozenset({binding.parent_run_id, binding.child_run_id})
        for row in rows:
            payload = _json_object_from_sql(row["payload"], context="mailbox slot")
            command = runtime_command_from_mailbox_payload(
                command_id=_sqlite_required_str(
                    row,
                    "slot_id",
                    context="mailbox slot",
                ),
                payload=payload,
            )
            if not runtime_command_targets(command, run_ids=run_ids):
                continue
            entries.append(
                CommandMailboxEntry(
                    command=command,
                    admitted_session_seq=format_u64(
                        _sqlite_required_int(
                            row,
                            "admitted_session_seq",
                            context="mailbox slot",
                        )
                    ),
                    admitted_dispatch_generation=format_u64(
                        _sqlite_required_int(
                            row,
                            "admitted_dispatch_generation",
                            context="mailbox slot",
                        )
                    ),
                    disposition=_sqlite_required_str(
                        row,
                        "disposition",
                        context="mailbox slot",
                    ),
                )
            )
        return tuple(entries)

    @staticmethod
    def _assert_no_pending_child_control(
        entries: tuple[CommandMailboxEntry, ...],
    ) -> None:
        for entry in entries:
            if runtime_command_invalidates_dispatch(entry.command):
                raise RecoveryLeaseConflictError(
                    "pending targeted control prevents child recovery"
                )

    async def settle_root_run(
        self,
        authority: OwnerAuthority,
        *,
        run_id: str,
        outcome: str,
        result: str | None,
        error: str | None,
        result_payload: JSONObject | None = None,
        extra_metadata: JSONObject | None = None,
    ) -> AuthoritativeCommit:
        from coding_agent.stores.runtime_store import SQLiteRuntimeStore

        run = await SQLiteRuntimeStore(self._path).load_agent_run(run_id)
        if run is None or run.session_id != authority.session_id:
            raise KeyError(f"root run not found: {run_id}")
        session = self.load_session(authority.session_id)
        if session is None:
            raise KeyError(f"session not found: {authority.session_id}")
        unit = build_root_settlement(
            run=run,
            session_state=session,
            outcome=outcome,
            result=result,
            error=error,
            result_payload=result_payload,
            extra_metadata=extra_metadata,
        )
        return await self.commit_authoritative_uow(authority, unit)

    async def admit_chat_command(
        self,
        authority: OwnerAuthority,
        *,
        prompt: str,
        command_id: str,
        parent_run_id: str | None,
        session_state: dict[str, object],
    ) -> ChatCommandAdmission:
        unit, admission = build_chat_admission(
            session_id=authority.session_id,
            prompt=prompt,
            command_id=command_id,
            parent_run_id=parent_run_id,
            session_state=session_state,
        )
        commit = await self.commit_authoritative_uow(authority, unit)
        return replace(
            admission,
            session_seq=commit.event.session_seq,
            idempotent=commit.idempotent,
        )

    async def admit_new_runtime_command(
        self,
        authority: OwnerAuthority,
        command: RuntimeCommand,
    ) -> RuntimeCommandAdmission:
        validate_new_runtime_command_target(command)
        return await self.admit_runtime_command(authority, command)

    async def admit_runtime_command(
        self,
        authority: OwnerAuthority,
        command: RuntimeCommand,
    ) -> RuntimeCommandAdmission:
        command_payload = runtime_command_mailbox_payload(command)
        invalidates_dispatch = runtime_command_invalidates_dispatch(command)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            fact_source = self._ensure_fact_source(connection, authority.session_id)
            existing = connection.execute(
                """
                SELECT * FROM session_mailbox_slots
                WHERE session_id = ? AND slot_id = ?
                """,
                (authority.session_id, command.command_id),
            ).fetchone()
            if existing is not None:
                admitted_session_seq = existing["admitted_session_seq"]
                admitted_generation = existing["admitted_dispatch_generation"]
                if admitted_session_seq is None or admitted_generation is None:
                    raise RuntimeCommandAdmissionConflictError(
                        "runtime command identity collides with a legacy mailbox slot"
                    )
                existing_payload = _json_object_from_sql(
                    existing["payload"],
                    context="runtime command mailbox slot",
                )
                if not runtime_command_mailbox_payloads_equal(
                    existing_payload,
                    command_payload,
                ):
                    raise RuntimeCommandAdmissionConflictError(
                        "runtime command identity was reused with different content"
                    )
                existing_command = runtime_command_from_mailbox_payload(
                    command_id=command.command_id,
                    payload=existing_payload,
                )
                return RuntimeCommandAdmission(
                    entry=CommandMailboxEntry(
                        command=existing_command,
                        admitted_session_seq=format_u64(
                            _sqlite_required_int(
                                existing,
                                "admitted_session_seq",
                                context="runtime command mailbox slot",
                            )
                        ),
                        admitted_dispatch_generation=format_u64(
                            _sqlite_required_int(
                                existing,
                                "admitted_dispatch_generation",
                                context="runtime command mailbox slot",
                            )
                        ),
                        disposition=_sqlite_required_str(
                            existing,
                            "disposition",
                            context="runtime command mailbox slot",
                        ),
                    ),
                    mailbox_cut=format_u64(fact_source.dispatch_generation_int),
                    idempotent=True,
                )

            admitted_session_seq = fact_source.session_seq_int + 1
            dispatch_generation = fact_source.dispatch_generation_int + int(
                invalidates_dispatch
            )
            connection.execute(
                """
                UPDATE session_fact_source
                SET session_seq = ?, dispatch_generation = ?
                WHERE session_id = ?
                """,
                (
                    admitted_session_seq,
                    dispatch_generation,
                    authority.session_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO session_mailbox_slots (
                    session_id,
                    slot_id,
                    lane,
                    disposition,
                    admitted_session_seq,
                    admitted_dispatch_generation,
                    payload
                )
                VALUES (?, ?, 'runtime', 'pending', ?, ?, ?)
                """,
                (
                    authority.session_id,
                    command.command_id,
                    admitted_session_seq,
                    dispatch_generation,
                    _json_to_sql(command_payload),
                ),
            )
            return RuntimeCommandAdmission(
                entry=CommandMailboxEntry(
                    command=command,
                    admitted_session_seq=format_u64(admitted_session_seq),
                    admitted_dispatch_generation=format_u64(dispatch_generation),
                    disposition="pending",
                ),
                mailbox_cut=format_u64(dispatch_generation),
            )

    async def load_operation_state(
        self,
        session_id: str,
        run_id: str,
    ) -> OperationStateVersion | None:
        _require_non_empty("session_id", session_id)
        _require_non_empty("run_id", run_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM session_operation_states
                WHERE session_id = ? AND run_id = ?
                """,
                (session_id, run_id),
            ).fetchone()
        return None if row is None else _operation_state_from_sqlite_row(row)

    async def load_transition_receipt(
        self,
        session_id: str,
        projection_epoch: int,
        transition_id: str,
    ) -> TransitionReceipt | None:
        _require_non_empty("session_id", session_id)
        _require_non_empty("transition_id", transition_id)
        if (
            isinstance(projection_epoch, bool)
            or not isinstance(projection_epoch, int)
            or projection_epoch < 0
        ):
            raise ValueError("projection_epoch must be a non-negative integer")
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM session_transition_receipts
                WHERE session_id = ? AND projection_epoch = ? AND transition_id = ?
                """,
                (session_id, projection_epoch, transition_id),
            ).fetchone()
        if row is None:
            return None
        fingerprint, result_payload = _transition_receipt_from_sqlite_row(row)
        _, _, receipt, _ = transition_commit_from_payload(
            session_id=session_id,
            transition_id=transition_id,
            projection_epoch=projection_epoch,
            mutation_fingerprint=fingerprint,
            payload=result_payload,
        )
        return receipt

    def _assert_reconciliation_preconditions(
        self,
        connection,
        authority: OwnerAuthority,
        unit: AuthoritativeUnitOfWork,
    ) -> None:
        if unit.reconciliation_evidence_ref is None:
            return
        mutation = next(
            (
                candidate
                for candidate in unit.normalized_effect_mutations
                if candidate.reconciliation is not None
            ),
            None,
        )
        if mutation is None:
            raise EffectMutationConflictError("reconciliation mutation is missing")
        current_effect = connection.execute(
            """
            SELECT status, payload FROM session_effect_slots
            WHERE session_id = ? AND effect_id = ?
            """,
            (authority.session_id, mutation.effect_id),
        ).fetchone()
        if current_effect is None or current_effect["status"] != "unknown":
            raise EffectMutationConflictError(
                "reconciliation requires a durable UNKNOWN effect"
            )
        current_payload = _json_object_from_sql(
            current_effect["payload"],
            context="effect slot",
        )
        expected_authorization = (
            unit.expected_reconciliation_authorization_transition_id
        )
        if (
            current_payload.get("attempt_id") != mutation.attempt_id
            or current_payload.get("authorization_transition_id")
            != expected_authorization
        ):
            raise EffectMutationConflictError(
                "reconciliation retained authorization does not match"
            )
        dispatch_owner_epoch = current_payload.get("dispatch_owner_epoch")
        if (
            isinstance(dispatch_owner_epoch, bool)
            or not isinstance(dispatch_owner_epoch, int)
            or dispatch_owner_epoch <= 0
        ):
            raise EffectMutationConflictError("effect dispatch owner epoch is missing")
        evidence_row = connection.execute(
            """
            SELECT payload FROM session_effect_reconciliation_evidence
            WHERE session_id = ? AND evidence_ref = ?
            """,
            (authority.session_id, unit.reconciliation_evidence_ref),
        ).fetchone()
        if evidence_row is None:
            raise EffectMutationConflictError("reconciliation evidence does not exist")
        evidence = _json_object_from_sql(
            evidence_row["payload"],
            context="effect reconciliation evidence",
        )
        expected_evidence = {
            "evidence_ref": unit.reconciliation_evidence_ref,
            "session_id": authority.session_id,
            "effect_id": mutation.effect_id,
            "attempt_id": mutation.attempt_id,
            "authorization_transition_id": expected_authorization,
            "reconciliation_owner_epoch": authority.epoch,
            "outcome": mutation.reconciliation.observed_outcome.value,
            "result": _plain_json(mutation.payload.get("result")),
            "reason_code": mutation.payload.get("reason_code"),
            "reason_message": mutation.payload.get("reason_message"),
        }
        if evidence != expected_evidence:
            raise EffectMutationConflictError(
                "reconciliation evidence does not match terminal payload"
            )
        executor_row = connection.execute(
            """
            SELECT payload FROM session_executor_attempts
            WHERE session_id = ? AND effect_id = ? AND attempt_id = ?
              AND authorization_transition_id = ?
            """,
            (
                authority.session_id,
                mutation.effect_id,
                mutation.attempt_id,
                expected_authorization,
            ),
        ).fetchone()
        if executor_row is None:
            raise ExecutorAttemptConflictError("executor attempt does not exist")
        executor = _executor_attempt_from_payload(
            _json_object_from_sql(
                executor_row["payload"],
                context="executor attempt",
            )
        )
        if executor.dispatch_owner_epoch != dispatch_owner_epoch:
            raise ExecutorAttemptConflictError(
                "executor attempt dispatch owner does not match effect"
            )
        same_owner_recovery = dispatch_owner_epoch == authority.epoch
        safe_unclaimed_takeover = (
            executor.status == "authorized_unclaimed"
            and dispatch_owner_epoch < authority.epoch
        )
        if (
            executor.status != "quiescent"
            and not same_owner_recovery
            and not safe_unclaimed_takeover
        ):
            raise ExecutorAttemptConflictError(
                "executor attempt is not durably quiescent"
            )
        state_cas = unit.state_cas
        state_value = unit.state_value
        if state_cas is None or state_value is None:
            raise ValueError("reconciliation transition is incomplete")
        current_state_row = connection.execute(
            """
            SELECT * FROM session_operation_states
            WHERE session_id = ? AND run_id = ?
            """,
            (authority.session_id, state_cas.run_id),
        ).fetchone()
        if current_state_row is None:
            raise StateVersionConflictError(
                "reconciliation operation state does not exist"
            )
        current_state = _operation_state_from_sqlite_row(current_state_row)
        if current_state.cas != state_cas:
            raise StateVersionConflictError("operation state compare-and-swap conflict")
        evidence_record = _reconciliation_evidence_from_payload(evidence)
        expected_state_value = state_value_with_reconciled_effect(
            current_state.value,
            evidence_record,
            mutation.reconciliation,
        )
        expected_runtime = expected_state_value["_agentkit_runtime"]
        expected_unknown = expected_runtime["unknown_effect"]
        if expected_unknown.get("dispatch_owner_epoch") != dispatch_owner_epoch:
            raise EffectMutationConflictError(
                "unknown effect dispatch owner does not match effect slot"
            )
        if _plain_json(state_value) != expected_state_value:
            raise EffectMutationConflictError(
                "reconciliation state does not match canonical evidence"
            )

    async def _commit_transition(
        self,
        authority: OwnerAuthority,
        unit: AuthoritativeUnitOfWork,
    ) -> AuthoritativeCommit:
        state_cas = unit.state_cas
        transition_id = unit.transition_id
        state_value = unit.state_value
        if state_cas is None or transition_id is None or state_value is None:
            raise ValueError("typed transition is incomplete")
        for fact in unit.facts:
            if fact.session_id != authority.session_id:
                raise SessionOwnershipConflictError(
                    "transition fact belongs to another session"
                )
        mutations = unit.normalized_effect_mutations
        for mutation in mutations:
            reconciliation = mutation.reconciliation
            if (
                reconciliation is not None
                and reconciliation.owner_epoch != authority.epoch
            ):
                raise SessionOwnershipConflictError(
                    "reconciliation owner epoch does not match authority"
                )
        mutation_fingerprint = transition_mutation_fingerprint(unit)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            fact_source = self._ensure_fact_source(connection, authority.session_id)
            if state_cas.projection_epoch != fact_source.projection_epoch_int:
                raise StateVersionConflictError(
                    "operation state projection epoch is stale"
                )
            receipt_row = connection.execute(
                """
                SELECT * FROM session_transition_receipts
                WHERE session_id = ? AND projection_epoch = ? AND transition_id = ?
                """,
                (
                    authority.session_id,
                    state_cas.projection_epoch,
                    transition_id,
                ),
            ).fetchone()
            if unit.adopt_transition_ids:
                adopted_loaded = [
                    (
                        adopt_transition_id,
                        connection.execute(
                            """
                            SELECT * FROM session_transition_receipts
                            WHERE session_id = ? AND projection_epoch = ?
                              AND transition_id = ?
                            """,
                            (
                                authority.session_id,
                                state_cas.projection_epoch,
                                adopt_transition_id,
                            ),
                        ).fetchone(),
                    )
                    for adopt_transition_id in unit.adopt_transition_ids
                ]
                parent_effect_status = None
                if mutations:
                    parent_effect = connection.execute(
                        """
                        SELECT status FROM session_effect_slots
                        WHERE session_id = ? AND effect_id = ?
                        """,
                        (authority.session_id, mutations[0].effect_id),
                    ).fetchone()
                    if parent_effect is not None:
                        parent_effect_status = parent_effect["status"]
                adopted = adopt_parent_settlement_receipt(
                    current_id=transition_id,
                    current_row=receipt_row,
                    adopted_rows=tuple(adopted_loaded),
                    parent_effect_status=parent_effect_status,
                )
                if adopted is not None:
                    adopted_id, adopted_row = adopted
                    stored_fingerprint, result_payload = (
                        _transition_receipt_from_sqlite_row(adopted_row)
                    )
                    if (
                        adopted_id == transition_id
                        and stored_fingerprint != mutation_fingerprint
                    ):
                        raise TransitionFingerprintMismatchError(
                            "transition mutation fingerprint mismatch"
                        )
                    state_version, facts, receipt, raw_cursor = (
                        transition_commit_from_payload(
                            session_id=authority.session_id,
                            transition_id=adopted_id,
                            projection_epoch=state_cas.projection_epoch,
                            mutation_fingerprint=stored_fingerprint,
                            payload=result_payload,
                        )
                    )
                    return AuthoritativeCommit(
                        event=None,
                        projection=fact_source.projection,
                        projection_epoch=format_u64(fact_source.projection_epoch_int),
                        raw_cursor=raw_cursor,
                        idempotent=True,
                        state_version=state_version,
                        facts=facts,
                        transition_receipt=receipt,
                    )
            elif receipt_row is not None:
                stored_fingerprint, result_payload = (
                    _transition_receipt_from_sqlite_row(receipt_row)
                )
                if stored_fingerprint != mutation_fingerprint:
                    raise TransitionFingerprintMismatchError(
                        "transition mutation fingerprint mismatch"
                    )
                state_version, facts, receipt, raw_cursor = (
                    transition_commit_from_payload(
                        session_id=authority.session_id,
                        transition_id=transition_id,
                        projection_epoch=state_cas.projection_epoch,
                        mutation_fingerprint=stored_fingerprint,
                        payload=result_payload,
                    )
                )
                return AuthoritativeCommit(
                    event=None,
                    projection=fact_source.projection,
                    projection_epoch=format_u64(fact_source.projection_epoch_int),
                    raw_cursor=raw_cursor,
                    idempotent=True,
                    state_version=state_version,
                    facts=facts,
                    transition_receipt=receipt,
                )

            if unit.recovery_guard is not None:
                guard = unit.recovery_guard
                binding_row = connection.execute(
                    """
                    SELECT payload FROM session_child_bindings
                    WHERE session_id = ? AND child_run_id = ?
                    """,
                    (authority.session_id, guard.child_run_id),
                ).fetchone()
                if binding_row is None:
                    raise StaleRecoveryGuardError(
                        "recovery guard child binding does not exist"
                    )
                guarded_binding = child_execution_binding_from_payload(
                    _json_object_from_sql(
                        binding_row["payload"],
                        context="child binding",
                    )
                )
                lease = guarded_binding.active_lease
                assert_recovery_guard_shape(unit, guarded_binding)
                if (
                    lease is None
                    or lease.lease_id != guard.lease_id
                    or lease.resume_generation != guard.resume_generation
                    or lease.resume_cut != guard.expected_recovery_cut
                    or lease.owner_epoch != authority.epoch
                    or fact_source.dispatch_generation_int
                    != parse_u64(
                        guard.expected_recovery_cut,
                        field_name="expected_recovery_cut",
                    )
                ):
                    raise StaleRecoveryGuardError(
                        "recovery guard no longer owns the exact fact cut"
                    )
            closeout_attempt: ExecutorAttemptRecord | None = None
            if unit.unstarted_dispatch_closeout is not None:
                closeout = unit.unstarted_dispatch_closeout
                closeout_row = connection.execute(
                    """
                    SELECT payload FROM session_executor_attempts
                    WHERE session_id = ? AND effect_id = ? AND attempt_id = ?
                      AND authorization_transition_id = ?
                    """,
                    (
                        authority.session_id,
                        closeout.effect_id,
                        closeout.attempt_id,
                        closeout.authorization_transition_id,
                    ),
                ).fetchone()
                if closeout_row is None:
                    raise ExecutorAttemptConflictError(
                        "unstarted closeout executor attempt does not exist"
                    )
                current_closeout = _executor_attempt_from_payload(
                    _json_object_from_sql(
                        closeout_row["payload"],
                        context="unstarted closeout executor attempt",
                    )
                )
                if (
                    current_closeout.status != "authorized_unclaimed"
                    or current_closeout.dispatch_owner_epoch > authority.epoch
                ):
                    raise ExecutorAttemptConflictError(
                        "unstarted closeout requires authorized_unclaimed attempt"
                    )
                closeout_attempt = replace(
                    current_closeout,
                    status="quiescent",
                    executor_id=closeout.executor_id,
                    claim_generation=1,
                    reservation_lease_expires_at=closeout.closed_at,
                    quiescence_evidence_ref=closeout.evidence_ref,
                )

            child_projection_row = connection.execute(
                """
                SELECT payload FROM session_child_bindings
                WHERE session_id = ? AND child_run_id = ?
                """,
                (authority.session_id, state_cas.run_id),
            ).fetchone()
            child_projection_binding = (
                None
                if child_projection_row is None
                else child_execution_binding_from_payload(
                    _json_object_from_sql(
                        child_projection_row["payload"],
                        context="child binding",
                    )
                )
            )
            facts_to_commit = tuple(
                (
                    fact
                    if child_projection_binding is None
                    else replace(
                        fact,
                        payload={
                            **fact.payload,
                            "run_id": (
                                child_projection_binding.parent_run_id
                                if fact.event_kind == "approval_requested"
                                else child_projection_binding.child_run_id
                            ),
                            "parent_run_id": (child_projection_binding.parent_run_id),
                            "parent_effect_id": (
                                child_projection_binding.parent_effect_id
                            ),
                            "subagent_child": True,
                            "skip_parent_context": True,
                            **(
                                {
                                    "target_run_id": (
                                        child_projection_binding.child_run_id
                                    ),
                                    "target_parent_effect_id": (
                                        child_projection_binding.parent_effect_id
                                    ),
                                }
                                if fact.event_kind == "approval_requested"
                                else {}
                            ),
                        },
                    )
                )
                for fact in unit.facts
            )

            for binding in unit.child_bindings:
                if binding.session_id != authority.session_id:
                    raise SessionOwnershipConflictError(
                        "child binding belongs to another session"
                    )
                existing_binding = connection.execute(
                    """
                    SELECT payload FROM session_child_bindings
                    WHERE session_id = ?
                      AND (
                        parent_effect_id = ?
                        OR child_run_id = ?
                      )
                    """,
                    (
                        authority.session_id,
                        binding.parent_effect_id,
                        binding.child_run_id,
                    ),
                ).fetchone()
                if existing_binding is not None:
                    stored_binding = child_execution_binding_from_payload(
                        _json_object_from_sql(
                            existing_binding["payload"],
                            context="child binding",
                        )
                    )
                    if stored_binding != binding:
                        raise ChildBindingConflictError(
                            "child binding identity changed content"
                        )
                    raise ChildBindingConflictError(
                        "child binding exists without a transition receipt"
                    )

            if unit.expected_mailbox_cut is not None:
                expected_mailbox_cut = parse_u64(
                    unit.expected_mailbox_cut,
                    field_name="expected_mailbox_cut",
                )
                current_mailbox_cut = fact_source.dispatch_generation_int
                if current_mailbox_cut != expected_mailbox_cut:
                    raise StaleMailboxCutError(
                        expected_mailbox_cut=expected_mailbox_cut,
                        current_mailbox_cut=current_mailbox_cut,
                    )
            self._assert_reconciliation_preconditions(
                connection,
                authority,
                unit,
            )

            current_state_row = connection.execute(
                """
                SELECT * FROM session_operation_states
                WHERE session_id = ? AND run_id = ?
                """,
                (authority.session_id, state_cas.run_id),
            ).fetchone()
            if current_state_row is None:
                if state_cas.revision != 0:
                    raise StateVersionConflictError("operation state revision is stale")
            else:
                current_state = _operation_state_from_sqlite_row(current_state_row)
                if current_state.cas != state_cas:
                    raise StateVersionConflictError(
                        "operation state compare-and-swap conflict"
                    )

            disposition_slots = {}
            for disposition in unit.dispositions:
                slot = mailbox_slot_from_disposition(disposition)
                existing_mailbox = connection.execute(
                    """
                    SELECT lane, disposition, payload FROM session_mailbox_slots
                    WHERE session_id = ? AND slot_id = ?
                    """,
                    (authority.session_id, slot.slot_id),
                ).fetchone()
                if existing_mailbox is None:
                    raise CommandDispositionConflictError(
                        "command disposition requires an admitted mailbox row"
                    )
                if existing_mailbox["disposition"] not in {"pending", "admitted"}:
                    raise CommandDispositionConflictError(
                        "command mailbox row is already terminal"
                    )
                existing_payload = _json_object_from_sql(
                    existing_mailbox["payload"],
                    context="mailbox slot",
                )
                disposition_slots[slot.slot_id] = replace(
                    slot,
                    lane=existing_mailbox["lane"],
                    payload={**existing_payload, **slot.payload},
                )

            effect_slots: dict[str, EffectLedgerSlot] = {}
            dispatch_mutation = next(
                (
                    mutation
                    for mutation in mutations
                    if mutation.expected_status is EffectStatus.PREPARED
                    and mutation.status is EffectStatus.DISPATCHED
                ),
                None,
            )
            for mutation in mutations:
                effect_slot = effect_slot_from_mutation(mutation)
                current_effect = connection.execute(
                    """
                    SELECT status, payload FROM session_effect_slots
                    WHERE session_id = ? AND effect_id = ?
                    """,
                    (authority.session_id, mutation.effect_id),
                ).fetchone()
                expected_status = mutation.expected_status
                if expected_status is None:
                    if current_effect is not None:
                        raise EffectMutationConflictError("effect already exists")
                elif current_effect is None:
                    raise EffectMutationConflictError("effect does not exist")
                else:
                    current_payload = _json_object_from_sql(
                        current_effect["payload"],
                        context="effect slot",
                    )
                    if current_effect["status"] != expected_status.value:
                        raise EffectMutationConflictError(
                            "effect status precondition failed"
                        )
                    if current_payload.get("attempt_id") != mutation.attempt_id:
                        raise EffectMutationConflictError(
                            "effect attempt precondition failed"
                        )
                    effect_slot = replace(
                        effect_slot,
                        payload={**current_payload, **effect_slot.payload},
                    )
                if mutation is dispatch_mutation:
                    effect_slot = replace(
                        effect_slot,
                        payload={
                            **effect_slot.payload,
                            "authorization_transition_id": transition_id,
                            "dispatch_owner_epoch": authority.epoch,
                        },
                    )
                effect_slots[effect_slot.effect_id] = effect_slot
            executor_attempt: ExecutorAttemptRecord | None = None
            if dispatch_mutation is not None:
                executor_attempt = ExecutorAttemptRecord(
                    session_id=authority.session_id,
                    effect_id=dispatch_mutation.effect_id,
                    attempt_id=dispatch_mutation.attempt_id,
                    authorization_transition_id=transition_id,
                    dispatch_owner_epoch=authority.epoch,
                    status="authorized_unclaimed",
                    authorization_mailbox_cut=unit.expected_mailbox_cut or "0",
                    authorization_mailbox_session_seq=format_u64(
                        fact_source.session_seq_int
                    ),
                )
                existing_executor = connection.execute(
                    """
                    SELECT payload FROM session_executor_attempts
                    WHERE session_id = ? AND effect_id = ? AND attempt_id = ?
                      AND authorization_transition_id = ?
                    """,
                    (
                        authority.session_id,
                        executor_attempt.effect_id,
                        executor_attempt.attempt_id,
                        transition_id,
                    ),
                ).fetchone()
                if existing_executor is not None:
                    raise ExecutorAttemptConflictError(
                        "dispatch executor attempt already exists without a receipt"
                    )
            first_seq = fact_source.session_seq_int + 1 if facts_to_commit else None
            committed_facts = [
                replace(
                    fact,
                    session_seq=format_u64(fact_source.session_seq_int + offset + 1),
                    projection_epoch=format_u64(fact_source.projection_epoch_int),
                )
                for offset, fact in enumerate(facts_to_commit)
            ]
            last_seq = (
                fact_source.session_seq_int + len(committed_facts)
                if committed_facts
                else None
            )
            raw_cursor = RawCursor(
                session_id=authority.session_id,
                session_seq=format_u64(
                    fact_source.session_seq_int if last_seq is None else last_seq
                ),
            )
            commit_ref = CommitRef(
                transition_id=transition_id,
                fact_seq_start=(None if first_seq is None else format_u64(first_seq)),
                fact_seq_end=None if last_seq is None else format_u64(last_seq),
            )
            state_version = OperationStateVersion(
                run_id=state_cas.run_id,
                revision=state_cas.revision + 1,
                projection_epoch=state_cas.projection_epoch,
                commit_ref=commit_ref,
                value=state_value,
            )
            notices = tuple(
                CommittedFactNotice(
                    fact_id=fact.event_id,
                    fact_kind=fact.event_kind,
                    payload=fact.payload,
                    session_seq=fact.session_seq,
                    projection_epoch=state_cas.projection_epoch,
                )
                for fact in committed_facts
            )
            receipt = TransitionReceipt(
                session_id=authority.session_id,
                projection_epoch=state_cas.projection_epoch,
                transition_id=transition_id,
                mutation_fingerprint=mutation_fingerprint,
                state_version=state_version,
                facts=notices,
                effect_plans=unit.effect_plans,
            )
            result_payload = transition_commit_payload(
                state_version=state_version,
                facts=tuple(committed_facts),
                raw_cursor=raw_cursor,
                effect_plans=unit.effect_plans,
            )
            connection.execute(
                """
                INSERT INTO session_transition_receipts (
                    session_id, projection_epoch, transition_id,
                    mutation_fingerprint, result
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    authority.session_id,
                    state_cas.projection_epoch,
                    transition_id,
                    mutation_fingerprint,
                    _json_to_sql(result_payload),
                ),
            )
            for fact in committed_facts:
                connection.execute(
                    """
                    INSERT INTO session_event_records (
                        session_id, session_seq, event_id, event_kind, payload,
                        created_at, projection_epoch
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        authority.session_id,
                        parse_u64(fact.session_seq, field_name="session_seq"),
                        fact.event_id,
                        fact.event_kind,
                        _json_to_sql(fact.payload),
                        _datetime_to_json(fact.created_at),
                        fact_source.projection_epoch_int,
                    ),
                )
            if last_seq is not None:
                connection.execute(
                    """
                    UPDATE session_fact_source
                    SET session_seq = ?
                    WHERE session_id = ?
                    """,
                    (last_seq, authority.session_id),
                )
            connection.execute(
                """
                INSERT INTO session_operation_states (
                    session_id, run_id, revision, projection_epoch, transition_id,
                    fact_seq_start, fact_seq_end, value
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, run_id)
                DO UPDATE SET
                    revision = excluded.revision,
                    projection_epoch = excluded.projection_epoch,
                    transition_id = excluded.transition_id,
                    fact_seq_start = excluded.fact_seq_start,
                    fact_seq_end = excluded.fact_seq_end,
                    value = excluded.value
                """,
                (
                    authority.session_id,
                    state_version.run_id,
                    state_version.revision,
                    state_version.projection_epoch,
                    transition_id,
                    first_seq,
                    last_seq,
                    _json_to_sql(_plain_json(state_version.value)),
                ),
            )
            for slot in disposition_slots.values():
                connection.execute(
                    """
                    INSERT INTO session_mailbox_slots (
                        session_id, slot_id, lane, disposition, payload
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(session_id, slot_id)
                    DO UPDATE SET
                        lane = excluded.lane,
                        disposition = excluded.disposition,
                        payload = excluded.payload
                    """,
                    (
                        authority.session_id,
                        slot.slot_id,
                        slot.lane,
                        slot.disposition,
                        _json_to_sql(slot.payload),
                    ),
                )
            for effect_slot in effect_slots.values():
                connection.execute(
                    """
                    INSERT INTO session_effect_slots (
                        session_id, effect_id, status, payload
                    )
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(session_id, effect_id)
                    DO UPDATE SET
                        status = excluded.status,
                        payload = excluded.payload
                    """,
                    (
                        authority.session_id,
                        effect_slot.effect_id,
                        effect_slot.status,
                        _json_to_sql(effect_slot.payload),
                    ),
                )
            if closeout_attempt is not None:
                connection.execute(
                    """
                    UPDATE session_executor_attempts SET status = ?, payload = ?
                    WHERE session_id = ? AND effect_id = ? AND attempt_id = ?
                      AND authorization_transition_id = ?
                    """,
                    (
                        closeout_attempt.status,
                        _json_to_sql(closeout_attempt.payload()),
                        closeout_attempt.session_id,
                        closeout_attempt.effect_id,
                        closeout_attempt.attempt_id,
                        closeout_attempt.authorization_transition_id,
                    ),
                )
            for binding in unit.child_bindings:
                connection.execute(
                    """
                    INSERT INTO session_child_bindings (
                        session_id, parent_effect_id, child_run_id, payload
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        authority.session_id,
                        binding.parent_effect_id,
                        binding.child_run_id,
                        _json_to_sql(child_execution_binding_payload(binding)),
                    ),
                )
            if executor_attempt is not None:
                connection.execute(
                    """
                    INSERT INTO session_executor_attempts (
                        session_id, effect_id, attempt_id,
                        authorization_transition_id, dispatch_owner_epoch,
                        status, payload
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        executor_attempt.session_id,
                        executor_attempt.effect_id,
                        executor_attempt.attempt_id,
                        executor_attempt.authorization_transition_id,
                        executor_attempt.dispatch_owner_epoch,
                        executor_attempt.status,
                        _json_to_sql(executor_attempt.payload()),
                    ),
                )
        return AuthoritativeCommit(
            event=None,
            projection=fact_source.projection,
            projection_epoch=format_u64(fact_source.projection_epoch_int),
            raw_cursor=raw_cursor,
            state_version=state_version,
            facts=tuple(committed_facts),
            transition_receipt=receipt,
        )

    async def commit_authoritative_uow(
        self,
        authority: OwnerAuthority,
        unit: AuthoritativeUnitOfWork,
    ) -> AuthoritativeCommit:
        _require_json_object("session payload", unit.session_state)
        if unit.session_state.get("id") != authority.session_id:
            raise SessionOwnershipConflictError(
                "session payload belongs to another owner"
            )
        payload_session_id = unit.session_state.get("session_id")
        if (
            payload_session_id is not None
            and payload_session_id != authority.session_id
        ):
            raise SessionOwnershipConflictError(
                "session payload belongs to another owner"
            )
        tape_id = unit.session_state.get("tape_id")
        if tape_id is not None and not isinstance(tape_id, str):
            raise TypeError("session payload tape_id must be a string")
        stored = await self.load_session_payload(authority.session_id)
        activation = await self.load_runtime_activation()
        stamped = stamp_session_payload_for_save(
            incoming=unit.session_state,
            stored=stored,
            activation=activation,
        )
        runtime_version = parse_runtime_version(stamped)
        if unit.effect is not None:
            assert_effect_status_allowed(
                status=unit.effect.status,
                runtime_version=runtime_version,
            )
        for mutation in unit.normalized_effect_mutations:
            assert_effect_status_allowed(
                status=mutation.status.value,
                runtime_version=runtime_version,
            )
        unit = replace(unit, session_state=stamped)
        if (
            unit.run_state is not None
            and unit.run_state.session_id != authority.session_id
        ):
            raise SessionOwnershipConflictError("agent run belongs to another session")
        if unit.is_transition:
            snapshot = snapshot_transition_unit(unit)
            return await self._commit_transition(authority, snapshot)
        if unit.event is None:
            raise ValueError("legacy unit of work requires an event")
        if unit.event.session_id != authority.session_id:
            raise SessionOwnershipConflictError("event belongs to another session")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            if unit.require_unsettled_root_run_id is not None:
                current_run = connection.execute(
                    "SELECT status FROM agent_runs WHERE run_id = ? AND session_id = ?",
                    (unit.require_unsettled_root_run_id, authority.session_id),
                ).fetchone()
                if current_run is None:
                    raise KeyError(
                        f"root run not found: {unit.require_unsettled_root_run_id}"
                    )
                if current_run["status"] in {
                    "completed",
                    "failed",
                    "cancelled",
                    "interrupted",
                }:
                    if unit.event.event_kind != "root_terminal":
                        raise RootRunAlreadySettledError(
                            "chat event after root settlement"
                        )
                    existing_terminal = connection.execute(
                        "SELECT * FROM session_event_records WHERE event_id = ?",
                        (unit.event.event_id,),
                    ).fetchone()
                    if existing_terminal is None:
                        raise ValueError("settled root run is missing root_terminal")
                    event = _event_record_from_sqlite_row(existing_terminal)
                    if event.payload != unit.event.payload:
                        raise RootRunAlreadySettledError(
                            "root run settled with a different outcome"
                        )
                    fact = self._ensure_fact_source(connection, authority.session_id)
                    if event.session_seq is None or event.projection_epoch is None:
                        raise ValueError("stored root_terminal is incomplete")
                    return AuthoritativeCommit(
                        event=event,
                        projection=fact.projection,
                        projection_epoch=event.projection_epoch,
                        raw_cursor=RawCursor(
                            session_id=authority.session_id,
                            session_seq=event.session_seq,
                        ),
                        idempotent=True,
                    )
            chat_receipt = (
                unit.receipt is not None
                and unit.receipt.receipt_id.startswith("chat-command:")
            )
            if chat_receipt:
                existing_receipt_row = connection.execute(
                    """
                    SELECT generation, payload, compensation_effect_id
                    FROM session_receipt_slots
                    WHERE session_id = ? AND receipt_id = ?
                    """,
                    (authority.session_id, unit.receipt.receipt_id),
                ).fetchone()
                if existing_receipt_row is not None:
                    if (
                        existing_receipt_row["generation"] != unit.receipt.generation
                        or _json_object_from_sql(
                            existing_receipt_row["payload"], context="receipt slot"
                        )
                        != unit.receipt.payload
                        or existing_receipt_row["compensation_effect_id"]
                        != unit.receipt.compensation_effect_id
                    ):
                        raise ChatCommandConflictError(
                            "command ID was reused with different input"
                        )
                    existing_event_row = connection.execute(
                        "SELECT * FROM session_event_records WHERE event_id = ?",
                        (unit.event.event_id,),
                    ).fetchone()
                    if existing_event_row is None:
                        raise ValueError(
                            "chat receipt references a missing admission event"
                        )
                    event = _event_record_from_sqlite_row(existing_event_row)
                    if event.session_id != authority.session_id:
                        raise SessionOwnershipConflictError(
                            "chat receipt event belongs to another session"
                        )
                    if event.session_seq is None or event.projection_epoch is None:
                        raise ValueError("stored admission event is incomplete")
                    fact = self._ensure_fact_source(connection, authority.session_id)
                    return AuthoritativeCommit(
                        event=event,
                        projection=fact.projection,
                        projection_epoch=event.projection_epoch,
                        raw_cursor=RawCursor(
                            session_id=authority.session_id,
                            session_seq=event.session_seq,
                        ),
                        idempotent=True,
                    )

                session_row = connection.execute(
                    "SELECT payload FROM agent_http_sessions WHERE session_id = ?",
                    (authority.session_id,),
                ).fetchone()
                if session_row is not None:
                    durable_session = _json_object_from_sql(
                        session_row["payload"], context="session payload"
                    )
                    active_run_id = durable_session.get("turn_id")
                    if active_run_id is not None:
                        if not isinstance(active_run_id, str):
                            raise ValueError("durable turn_id must be a string")
                        active_run = connection.execute(
                            "SELECT status FROM agent_runs WHERE run_id = ? AND session_id = ?",
                            (active_run_id, authority.session_id),
                        ).fetchone()
                        if active_run is None or active_run["status"] in {
                            "queued",
                            "requested",
                            "claimed",
                            "running",
                            "cancelling",
                        }:
                            raise TurnInProgressError("a root turn is already active")

            run_state = unit.run_state
            if unit.require_settled_parent_run_id is not None:
                parent = connection.execute(
                    """
                    SELECT * FROM agent_runs
                    WHERE session_id = ? AND superseded_by_checkpoint_id IS NULL
                    ORDER BY started_at DESC, run_id DESC
                    LIMIT 1
                    """,
                    (authority.session_id,),
                ).fetchone()
                if (
                    parent is None
                    or parent["run_id"] != unit.require_settled_parent_run_id
                    or parent["status"]
                    not in {"completed", "failed", "cancelled", "interrupted"}
                ):
                    raise ResumeSourceUnsettledError(
                        "resume source is not the latest active settled run"
                    )
                if run_state is None:
                    raise ValueError("resume admission requires a run")
                metadata = dict(run_state.metadata)
                metadata.update(
                    {
                        "previous_run_id": parent["run_id"],
                        "resume_from_run_id": parent["run_id"],
                        "resume_reason": "user_resume",
                        "resume_context_injected": True,
                    }
                )
                parent_metadata = _json_object_from_sql(
                    parent["metadata"], context="run metadata"
                )
                previous_event_id = parent_metadata.get("last_event_id")
                if previous_event_id is not None:
                    if not isinstance(previous_event_id, str) or not previous_event_id:
                        raise ValueError("last_event_id must be a non-empty string")
                    metadata["resume_from_event_id"] = previous_event_id
                run_state = replace(run_state, metadata=metadata)
            if tape_id:
                self._bind_tape(connection, authority.session_id, tape_id)
            if run_state is not None:
                if run_state.tape_id is None:
                    raise SessionOwnershipConflictError(
                        "run target is not bound to a tape"
                    )
                self._assert_tape_belongs_to_session(
                    connection,
                    tape_id=run_state.tape_id,
                    session_id=authority.session_id,
                )
            fact = self._ensure_fact_source(connection, authority.session_id)
            existing_row = connection.execute(
                """
                SELECT * FROM session_event_records
                WHERE event_id = ?
                """,
                (unit.event.event_id,),
            ).fetchone()
            idempotent = False
            if existing_row is not None:
                existing_event = _event_record_from_sqlite_row(existing_row)
                if existing_event.session_id != authority.session_id:
                    raise SessionOwnershipConflictError(
                        "event belongs to another session"
                    )
                if existing_event.session_seq is None:
                    raise ValueError("existing event must include session_seq")
                if existing_event.projection_epoch is None:
                    raise ValueError("existing event must include projection_epoch")
                next_seq = parse_u64(
                    existing_event.session_seq, field_name="session_seq"
                )
                existing_epoch = parse_u64(
                    existing_event.projection_epoch, field_name="projection_epoch"
                )
                if existing_epoch != fact.projection_epoch_int:
                    connection.execute(
                        """
                        UPDATE session_event_records
                        SET projection_epoch = ?
                        WHERE event_id = ?
                        """,
                        (fact.projection_epoch_int, unit.event.event_id),
                    )
                    promoted_row = connection.execute(
                        """
                        SELECT * FROM session_event_records
                        WHERE event_id = ?
                        """,
                        (unit.event.event_id,),
                    ).fetchone()
                    if promoted_row is None:
                        raise RuntimeError("failed to promote event projection_epoch")
                    event = _event_record_from_sqlite_row(promoted_row)
                else:
                    event = existing_event
                idempotent = True
            else:
                next_seq = fact.session_seq_int + 1
                connection.execute(
                    """
                    UPDATE session_fact_source
                    SET session_seq = ?
                    WHERE session_id = ?
                    """,
                    (next_seq, authority.session_id),
                )
                connection.execute(
                    """
                    INSERT INTO session_event_records (
                        session_id, session_seq, event_id, event_kind, payload,
                        created_at, projection_epoch
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        authority.session_id,
                        next_seq,
                        unit.event.event_id,
                        unit.event.event_kind,
                        _json_to_sql(unit.event.payload),
                        _datetime_to_json(unit.event.created_at),
                        fact.projection_epoch_int,
                    ),
                )
                event = EventRecord(
                    event_id=unit.event.event_id,
                    session_id=authority.session_id,
                    event_kind=unit.event.event_kind,
                    payload=unit.event.payload,
                    created_at=unit.event.created_at,
                    session_seq=format_u64(next_seq),
                    projection_epoch=format_u64(fact.projection_epoch_int),
                )
            connection.execute(
                """
                INSERT INTO agent_http_sessions (session_id, payload, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(session_id)
                DO UPDATE SET payload = excluded.payload,
                              updated_at = CURRENT_TIMESTAMP
                """,
                (authority.session_id, json.dumps(unit.session_state, sort_keys=True)),
            )
            if run_state is not None:
                existing = connection.execute(
                    "SELECT session_id FROM agent_runs WHERE run_id = ?",
                    (run_state.run_id,),
                ).fetchone()
                if (
                    existing is not None
                    and existing["session_id"] != authority.session_id
                ):
                    raise SessionOwnershipConflictError(
                        "agent run target belongs to another session"
                    )
                connection.execute(
                    """
                    INSERT INTO agent_runs (
                        run_id, session_id, tape_id, parent_run_id, agent_id, status,
                        started_at, ended_at, metadata, result, error,
                        superseded_by_checkpoint_id, superseded_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id)
                    DO UPDATE SET
                        tape_id = excluded.tape_id,
                        parent_run_id = excluded.parent_run_id,
                        agent_id = excluded.agent_id,
                        status = excluded.status,
                        started_at = excluded.started_at,
                        ended_at = excluded.ended_at,
                        metadata = excluded.metadata,
                        result = excluded.result,
                        error = excluded.error
                    """,
                    _agent_run_sqlite_values(run_state),
                )
            if unit.mailbox is not None:
                connection.execute(
                    """
                    INSERT INTO session_mailbox_slots (
                        session_id, slot_id, lane, disposition, payload
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(session_id, slot_id)
                    DO UPDATE SET
                        lane = excluded.lane,
                        disposition = excluded.disposition,
                        payload = excluded.payload
                    """,
                    (
                        authority.session_id,
                        unit.mailbox.slot_id,
                        unit.mailbox.lane,
                        unit.mailbox.disposition,
                        _json_to_sql(unit.mailbox.payload),
                    ),
                )
            if unit.effect is not None:
                existing_effect = connection.execute(
                    """
                    SELECT status FROM session_effect_slots
                    WHERE session_id = ? AND effect_id = ?
                    """,
                    (authority.session_id, unit.effect.effect_id),
                ).fetchone()
                current_status = (
                    None if existing_effect is None else str(existing_effect["status"])
                )
                if unit.effect.status == "settled":
                    allowed = legacy_settled_slot_may_replace(current_status)
                else:
                    allowed = current_status is None or effect_status_may_replace(
                        current=current_status,
                        incoming=unit.effect.status,
                    )
                if allowed:
                    connection.execute(
                        """
                        INSERT INTO session_effect_slots (
                            session_id, effect_id, status, payload
                        )
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(session_id, effect_id)
                        DO UPDATE SET
                            status = excluded.status,
                            payload = excluded.payload
                        """,
                        (
                            authority.session_id,
                            unit.effect.effect_id,
                            unit.effect.status,
                            _json_to_sql(unit.effect.payload),
                        ),
                    )
            if unit.receipt is not None:
                existing_receipt = connection.execute(
                    """
                    SELECT generation FROM session_receipt_slots
                    WHERE session_id = ? AND receipt_id = ?
                    """,
                    (authority.session_id, unit.receipt.receipt_id),
                ).fetchone()
                if existing_receipt is None or receipt_generation_may_replace(
                    current=existing_receipt["generation"],
                    incoming=unit.receipt.generation,
                ):
                    connection.execute(
                        """
                        INSERT INTO session_receipt_slots (
                            session_id, receipt_id, generation, payload,
                            compensation_effect_id
                        )
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(session_id, receipt_id)
                        DO UPDATE SET
                            generation = excluded.generation,
                            payload = excluded.payload,
                            compensation_effect_id = excluded.compensation_effect_id
                        """,
                        (
                            authority.session_id,
                            unit.receipt.receipt_id,
                            unit.receipt.generation,
                            _json_to_sql(unit.receipt.payload),
                            unit.receipt.compensation_effect_id,
                        ),
                    )
        return AuthoritativeCommit(
            event=event,
            projection=fact.projection,
            projection_epoch=format_u64(fact.projection_epoch_int),
            raw_cursor=RawCursor(
                session_id=authority.session_id,
                session_seq=format_u64(next_seq),
            ),
            idempotent=idempotent,
        )


def _executor_attempt_from_payload(payload: JSONObject) -> ExecutorAttemptRecord:
    lease = payload.get("reservation_lease_expires_at")
    return ExecutorAttemptRecord(
        session_id=_required_payload_str(payload, "session_id"),
        effect_id=_required_payload_str(payload, "effect_id"),
        attempt_id=_required_payload_str(payload, "attempt_id"),
        authorization_transition_id=_required_payload_str(
            payload,
            "authorization_transition_id",
        ),
        dispatch_owner_epoch=_required_payload_int(
            payload,
            "dispatch_owner_epoch",
        ),
        status=_required_payload_str(payload, "status"),
        authorization_mailbox_cut=(
            _optional_payload_str(payload, "authorization_mailbox_cut") or "0"
        ),
        authorization_mailbox_session_seq=(
            _optional_payload_str(
                payload,
                "authorization_mailbox_session_seq",
            )
            or "0"
        ),
        executor_id=_optional_payload_str(payload, "executor_id"),
        claim_generation=_required_payload_int(payload, "claim_generation"),
        reservation_lease_expires_at=(
            None
            if lease is None
            else datetime.fromisoformat(
                _required_payload_str(payload, "reservation_lease_expires_at")
            )
        ),
        quiescence_evidence_ref=_optional_payload_str(
            payload,
            "quiescence_evidence_ref",
        ),
    )


def _required_payload_str(payload: JSONObject, field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _optional_payload_str(payload: JSONObject, field_name: str) -> str | None:
    value = payload.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string or None")
    return value


def _required_payload_int(payload: JSONObject, field_name: str) -> int:
    value = payload.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _reconciliation_evidence_from_payload(
    payload: JSONObject,
) -> EffectReconciliationEvidence:
    return EffectReconciliationEvidence(
        evidence_ref=_required_payload_str(payload, "evidence_ref"),
        session_id=_required_payload_str(payload, "session_id"),
        effect_id=_required_payload_str(payload, "effect_id"),
        attempt_id=_required_payload_str(payload, "attempt_id"),
        authorization_transition_id=_required_payload_str(
            payload,
            "authorization_transition_id",
        ),
        reconciliation_owner_epoch=_required_payload_int(
            payload,
            "reconciliation_owner_epoch",
        ),
        outcome=_required_payload_str(payload, "outcome"),
        result=payload.get("result"),
        reason_code=_optional_payload_str(payload, "reason_code"),
        reason_message=_optional_payload_str(payload, "reason_message"),
    )
