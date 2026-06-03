from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

from coding_agent.runtime_store import AgentRunRecord, JSONObject, RuntimeEventRecord
from coding_agent.stores import RuntimeRunStore

from .lifecycle import RuntimeRunResumeContext
from .metadata import RuntimeMetadataSession
from .runtime_events import runtime_event_correlation_from_run
from .runtime_events import with_runtime_event_correlation

ATTACHED_EXECUTOR_BINDING_KINDS = frozenset({"external_worker", "local_attached"})
ATTACHED_EXECUTOR_FINAL_STATUSES = frozenset({"completed", "cancelled", "failed"})


class RuntimeAttachedExecutorStore(RuntimeRunStore, Protocol):
    async def append_runtime_event(
        self,
        record: RuntimeEventRecord,
    ) -> RuntimeEventRecord: ...


class RuntimeAttachedExecutorSession(RuntimeMetadataSession, Protocol):
    tape_id: str | None


class RuntimeAttachedExecutorMetadataProvider(Protocol):
    def __call__(
        self,
        session: RuntimeAttachedExecutorSession,
        *,
        resume_context: RuntimeRunResumeContext | None = None,
    ) -> JSONObject: ...


@dataclass(frozen=True)
class RuntimeAttachedExecutorClaim:
    run: AgentRunRecord
    claim_token: str
    prompt: str


@dataclass(frozen=True)
class RuntimeAttachedExecutorService:
    store: RuntimeAttachedExecutorStore | None
    metadata_for_session: RuntimeAttachedExecutorMetadataProvider
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    token_urlsafe: Callable[[int], str] = secrets.token_urlsafe

    async def request_run(
        self,
        session: RuntimeAttachedExecutorSession,
        *,
        prompt: str,
        run_id: str,
        resume_context: RuntimeRunResumeContext | None = None,
    ) -> AgentRunRecord:
        requested_at = self.now()
        metadata = self.metadata_for_session(session, resume_context=resume_context)
        metadata["prompt"] = prompt
        metadata["requested_at"] = requested_at.isoformat()
        metadata["run_request_status"] = "requested"
        return await self._require_store().create_agent_run(
            AgentRunRecord(
                run_id=run_id,
                session_id=session.id,
                tape_id=session.tape_id,
                parent_run_id=(
                    None if resume_context is None else resume_context.previous_run_id
                ),
                agent_id=None,
                status="requested",
                started_at=requested_at,
                metadata=metadata,
                result={},
                error=None,
            )
        )

    async def claim_run(
        self,
        *,
        executor_id: str,
        executor_kind: str,
        session_id: str | None = None,
        lease_seconds: int = 30,
        worker_instance_id: str | None = None,
        process_id: int | None = None,
        capabilities: JSONObject | None = None,
        workspace_sync: JSONObject | None = None,
    ) -> RuntimeAttachedExecutorClaim | None:
        claim_token = self.token_urlsafe(32)
        now = self.now()
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        claim_metadata: JSONObject = {
            "worker_id": executor_id,
            "executor_id": executor_id,
            "claim_token_hash": _hash_claim_token(claim_token),
            "claimed_at": now.isoformat(),
            "lease_expires_at": lease_expires_at.isoformat(),
        }
        if worker_instance_id is not None:
            claim_metadata["worker_instance_id"] = worker_instance_id
        if process_id is not None:
            claim_metadata["process_id"] = process_id
        if capabilities is not None:
            claim_metadata["capabilities"] = capabilities
        if workspace_sync is not None:
            claim_metadata["workspace_sync"] = workspace_sync
        run = await self._require_store().claim_attached_executor_run(
            session_id=session_id,
            executor_kind=executor_kind,
            claim_metadata=claim_metadata,
        )
        if run is None:
            return None
        return RuntimeAttachedExecutorClaim(
            run=run,
            claim_token=claim_token,
            prompt=_metadata_required_str(run.metadata, "prompt"),
        )

    async def heartbeat_run(
        self,
        *,
        run_id: str,
        executor_id: str,
        claim_token: str,
        lease_seconds: int = 30,
        worker_instance_id: str | None = None,
        process_id: int | None = None,
        capabilities: JSONObject | None = None,
        workspace_sync: JSONObject | None = None,
    ) -> AgentRunRecord:
        run = await self.load_and_authorize_run(
            run_id=run_id,
            executor_id=executor_id,
            claim_token=claim_token,
        )
        metadata = dict(run.metadata)
        metadata["lease_expires_at"] = (
            self.now() + timedelta(seconds=lease_seconds)
        ).isoformat()
        metadata["last_heartbeat_at"] = self.now().isoformat()
        if worker_instance_id is not None:
            metadata["worker_instance_id"] = worker_instance_id
        if process_id is not None:
            metadata["process_id"] = process_id
        if capabilities is not None:
            metadata["capabilities"] = capabilities
        if workspace_sync is not None:
            metadata["workspace_sync"] = workspace_sync
        status = "running" if run.status == "claimed" else run.status
        return await self._require_store().update_agent_run(
            run_id,
            status=status,
            ended_at=run.ended_at,
            metadata=cast(JSONObject, metadata),
            result=run.result,
            error=run.error,
        )

    async def append_event(
        self,
        *,
        run_id: str,
        executor_id: str,
        claim_token: str,
        event_id: str,
        event_kind: str,
        payload: JSONObject,
        created_at: datetime,
    ) -> RuntimeEventRecord:
        run = await self.load_and_authorize_run(
            run_id=run_id,
            executor_id=executor_id,
            claim_token=claim_token,
        )
        return await self._require_store().append_runtime_event(
            RuntimeEventRecord(
                event_id=event_id,
                run_id=run_id,
                event_kind=event_kind,
                payload=with_runtime_event_correlation(
                    payload,
                    runtime_event_correlation_from_run(run),
                ),
                created_at=created_at,
            )
        )

    async def finalize_run(
        self,
        *,
        run_id: str,
        executor_id: str,
        claim_token: str,
        status: str,
        result: JSONObject,
        error: str | None,
        tape_id: str | None,
    ) -> AgentRunRecord:
        run = await self.load_and_authorize_run(
            run_id=run_id,
            executor_id=executor_id,
            claim_token=claim_token,
        )
        return await self.finalize_authorized_run(
            run,
            status=status,
            result=result,
            error=error,
            tape_id=tape_id,
        )

    async def finalize_authorized_run(
        self,
        run: AgentRunRecord,
        *,
        status: str,
        result: JSONObject,
        error: str | None,
        tape_id: str | None,
    ) -> AgentRunRecord:
        self.validate_final_status(status)
        metadata = dict(run.metadata)
        metadata["finalized_at"] = self.now().isoformat()
        if tape_id is not None:
            metadata["final_tape_id"] = tape_id
        return await self._require_store().update_agent_run(
            run.run_id,
            status=status,
            ended_at=self.now(),
            metadata=cast(JSONObject, metadata),
            result=result,
            error=error,
        )

    def validate_final_status(self, status: str) -> None:
        if status not in ATTACHED_EXECUTOR_FINAL_STATUSES:
            raise ValueError("attached executor final status is invalid")

    async def load_and_authorize_run(
        self,
        *,
        run_id: str,
        executor_id: str,
        claim_token: str,
    ) -> AgentRunRecord:
        run = await self._require_store().load_agent_run(run_id)
        if run is None:
            raise KeyError(f"runtime run not found: {run_id}")
        metadata = run.metadata
        if (
            metadata.get("execution_binding_kind")
            not in ATTACHED_EXECUTOR_BINDING_KINDS
        ):
            raise ValueError("runtime run is not attached executor owned")
        owner_id = metadata.get("executor_id") or metadata.get("worker_id")
        if owner_id != executor_id:
            raise PermissionError("attached executor does not own this run")
        token_hash = metadata.get("claim_token_hash")
        if not isinstance(token_hash, str) or not secrets.compare_digest(
            token_hash,
            _hash_claim_token(claim_token),
        ):
            raise PermissionError("attached executor claim token is invalid")
        if run.status not in {"claimed", "running", "cancelling"}:
            raise PermissionError("attached executor claim is expired or inactive")
        lease_expires_at = _optional_metadata_datetime(metadata, "lease_expires_at")
        if lease_expires_at is None or lease_expires_at <= self.now():
            raise PermissionError("attached executor claim is expired or inactive")
        return run

    def _require_store(self) -> RuntimeAttachedExecutorStore:
        if self.store is None:
            raise RuntimeError("runtime store is not configured")
        return self.store


def _hash_claim_token(claim_token: str) -> str:
    return hashlib.sha256(claim_token.encode("utf-8")).hexdigest()


def _metadata_required_str(metadata: Mapping[str, object], key: str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"runtime run metadata is missing {key}")
    return value


def _optional_metadata_datetime(
    metadata: Mapping[str, object],
    key: str,
) -> datetime | None:
    value = metadata.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"runtime run metadata {key} must be a non-empty string")
    return datetime.fromisoformat(value)


__all__ = [
    "ATTACHED_EXECUTOR_BINDING_KINDS",
    "RuntimeAttachedExecutorClaim",
    "RuntimeAttachedExecutorSession",
    "RuntimeAttachedExecutorService",
    "RuntimeAttachedExecutorStore",
]
