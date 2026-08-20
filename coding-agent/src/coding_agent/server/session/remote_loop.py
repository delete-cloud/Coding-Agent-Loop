"""Retired attached/external-worker remote-loop entrypoints."""

from __future__ import annotations

import logging
from datetime import datetime
from coding_agent.stores.runtime_store import (
    AgentRunRecord,
    JSONObject,
    RuntimeEventRecord,
)
from coding_agent.runs import (
    RemoteLoopOwnershipRetired,
    RuntimeResumeContext as SessionResumeContext,
)
from coding_agent.server.session.models import ExternalWorkerClaim

logger = logging.getLogger("coding_agent.server.session_manager")


class RemoteLoopOps:
    async def request_attached_executor_run(
        self,
        session_id: str,
        prompt: str,
        *,
        run_id: str | None = None,
        resume_context: SessionResumeContext | None = None,
    ) -> AgentRunRecord:
        del session_id, prompt, run_id, resume_context
        raise RemoteLoopOwnershipRetired()

    async def request_external_worker_run(
        self,
        session_id: str,
        prompt: str,
        *,
        run_id: str | None = None,
        resume_context: SessionResumeContext | None = None,
    ) -> AgentRunRecord:
        """Compatibility wrapper for the legacy external-worker API."""
        return await self.request_attached_executor_run(
            session_id,
            prompt,
            run_id=run_id,
            resume_context=resume_context,
        )

    async def claim_attached_executor_run(
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
    ) -> ExternalWorkerClaim | None:
        del (
            executor_id,
            executor_kind,
            session_id,
            lease_seconds,
            worker_instance_id,
            process_id,
            capabilities,
            workspace_sync,
        )
        raise RemoteLoopOwnershipRetired()

    async def claim_external_worker_run(
        self,
        *,
        worker_id: str,
        executor_kind: str,
        session_id: str | None = None,
        lease_seconds: int = 30,
        worker_instance_id: str | None = None,
        process_id: int | None = None,
        capabilities: JSONObject | None = None,
        workspace_sync: JSONObject | None = None,
    ) -> ExternalWorkerClaim | None:
        """Compatibility wrapper for the legacy external-worker API."""
        return await self.claim_attached_executor_run(
            executor_id=worker_id,
            executor_kind=executor_kind,
            session_id=session_id,
            lease_seconds=lease_seconds,
            worker_instance_id=worker_instance_id,
            process_id=process_id,
            capabilities=capabilities,
            workspace_sync=workspace_sync,
        )

    async def heartbeat_attached_executor_run(
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
        del (
            run_id,
            executor_id,
            claim_token,
            lease_seconds,
            worker_instance_id,
            process_id,
            capabilities,
            workspace_sync,
        )
        raise RemoteLoopOwnershipRetired()

    async def heartbeat_external_worker_run(
        self,
        *,
        run_id: str,
        worker_id: str,
        claim_token: str,
        lease_seconds: int = 30,
        worker_instance_id: str | None = None,
        process_id: int | None = None,
        capabilities: JSONObject | None = None,
        workspace_sync: JSONObject | None = None,
    ) -> AgentRunRecord:
        """Compatibility wrapper for the legacy external-worker API."""
        return await self.heartbeat_attached_executor_run(
            run_id=run_id,
            executor_id=worker_id,
            claim_token=claim_token,
            lease_seconds=lease_seconds,
            worker_instance_id=worker_instance_id,
            process_id=process_id,
            capabilities=capabilities,
            workspace_sync=workspace_sync,
        )

    async def append_attached_executor_event(
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
        del (
            run_id,
            executor_id,
            claim_token,
            event_id,
            event_kind,
            payload,
            created_at,
        )
        raise RemoteLoopOwnershipRetired()

    async def append_external_worker_event(
        self,
        *,
        run_id: str,
        worker_id: str,
        claim_token: str,
        event_id: str,
        event_kind: str,
        payload: JSONObject,
        created_at: datetime,
    ) -> RuntimeEventRecord:
        """Compatibility wrapper for the legacy external-worker API."""
        return await self.append_attached_executor_event(
            run_id=run_id,
            executor_id=worker_id,
            claim_token=claim_token,
            event_id=event_id,
            event_kind=event_kind,
            payload=payload,
            created_at=created_at,
        )

    async def finalize_attached_executor_run(
        self,
        *,
        run_id: str,
        executor_id: str,
        claim_token: str,
        status: str,
        result: JSONObject,
        error: str | None,
        tape_id: str | None,
        tape_entries: list[JSONObject] | None = None,
    ) -> AgentRunRecord:
        del (
            run_id,
            executor_id,
            claim_token,
            status,
            result,
            error,
            tape_id,
            tape_entries,
        )
        raise RemoteLoopOwnershipRetired()

    async def finalize_external_worker_run(
        self,
        *,
        run_id: str,
        worker_id: str,
        claim_token: str,
        status: str,
        result: JSONObject,
        error: str | None,
        tape_id: str | None,
        tape_entries: list[JSONObject] | None = None,
    ) -> AgentRunRecord:
        """Compatibility wrapper for the legacy external-worker API."""
        return await self.finalize_attached_executor_run(
            run_id=run_id,
            executor_id=worker_id,
            claim_token=claim_token,
            status=status,
            result=result,
            error=error,
            tape_id=tape_id,
            tape_entries=tape_entries,
        )
