"""Cloud workspace metadata, cleanup, and archive export."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from coding_agent.runs import CloudWorkspaceRef
from coding_agent.server.stores.workspace_store import (
    JSONValue,
    WorkspaceRecord,
    WorkspaceRetentionPolicy,
    WorkspaceStatus,
)
from coding_agent.server.session.models import Session
from coding_agent.server.session.models import T
from coding_agent.server.session.models import WorkspaceMetadataStoreProtocol
from coding_agent.server.session.models import _session_cloud_workspace
from coding_agent.server.session import _bindings

logger = logging.getLogger("coding_agent.server.session_manager")


class WorkspaceOps:
    def configure_workspace_metadata_store(
        self,
        workspace_metadata_store: WorkspaceMetadataStoreProtocol | None,
    ) -> None:
        self._workspace_metadata_store = workspace_metadata_store

    async def list_workspace_records(self) -> list[WorkspaceRecord]:
        if self._workspace_metadata_store is None:
            raise RuntimeError("workspace metadata store is not configured")
        return await self._workspace_metadata_store.list()

    async def load_workspace_record_by_workspace_id(
        self, workspace_id: str
    ) -> WorkspaceRecord | None:
        if self._workspace_metadata_store is None:
            raise RuntimeError("workspace metadata store is not configured")
        return await self._workspace_metadata_store.load_by_workspace_id(workspace_id)

    async def update_workspace_record_status(
        self,
        workspace_record_id: str,
        *,
        status: WorkspaceStatus,
        cleanup_error: str | None = None,
    ) -> None:
        if self._workspace_metadata_store is None:
            raise RuntimeError("workspace metadata store is not configured")
        await self._workspace_metadata_store.update_status(
            workspace_record_id,
            status=status,
            cleanup_error=cleanup_error,
        )

    async def update_workspace_record_retention(
        self,
        workspace_record_id: str,
        *,
        retention_policy: WorkspaceRetentionPolicy,
        expires_at: datetime | None,
        status: WorkspaceStatus,
    ) -> None:
        if self._workspace_metadata_store is None:
            raise RuntimeError("workspace metadata store is not configured")
        await self._workspace_metadata_store.update_retention(
            workspace_record_id,
            retention_policy=retention_policy,
            expires_at=expires_at,
            status=status,
        )

    async def update_workspace_record_result_refs(
        self,
        workspace_record_id: str,
        *,
        result_refs: dict[str, JSONValue],
    ) -> None:
        if self._workspace_metadata_store is None:
            raise RuntimeError("workspace metadata store is not configured")
        await self._workspace_metadata_store.update_result_refs(
            workspace_record_id,
            result_refs=result_refs,
        )

    def _session_uses_provisioned_cloud_workspace(self, session: Session) -> bool:
        origin = session.origin
        return (
            _session_cloud_workspace(session) is not None
            and origin is not None
            and origin.get("placement_kind") == "cloud_workspace"
            and origin.get("workspace_source_kind") is not None
        )

    def _cleanup_provisioned_cloud_binding(self, session: Session) -> None:
        if self._provisioned_cloud_binding_cleanup is None:
            return
        if not self._session_uses_provisioned_cloud_workspace(session):
            return
        workspace = _session_cloud_workspace(session)
        if workspace is None:
            return
        self._provisioned_cloud_binding_cleanup(workspace)

    async def _cleanup_provisioned_cloud_binding_async(
        self, session: Session
    ) -> str | None:
        try:
            await _bindings.module().asyncio.to_thread(
                self._cleanup_provisioned_cloud_binding, session
            )
            return None
        except Exception as exc:
            logger.exception(
                "Failed to clean up provisioned cloud workspace for session %s",
                session.id,
            )
            return str(exc) or "provisioned cloud workspace cleanup failed"

    async def _workspace_record_for_session(
        self, session: Session
    ) -> WorkspaceRecord | None:
        if self._workspace_metadata_store is None:
            return None
        workspace = _session_cloud_workspace(session)
        if workspace is None:
            return None
        return await self._workspace_metadata_store.load_for_session_workspace(
            session_id=session.id,
            workspace_id=workspace.workspace_id,
        )

    async def _finalize_provisioned_cloud_workspace_on_close(
        self, session: Session
    ) -> None:
        if not self._session_uses_provisioned_cloud_workspace(session):
            return

        record = await self._workspace_record_for_session(session)
        store = self._workspace_metadata_store
        if record is not None and record.retention_policy != "delete_on_close":
            if store is None:
                raise RuntimeError("workspace metadata store is not configured")
            await store.update_status(
                record.workspace_record_id,
                status="retained",
                cleanup_error=None,
            )
            return

        cleanup_error = await self._cleanup_provisioned_cloud_binding_async(session)
        if record is None:
            return
        if store is None:
            raise RuntimeError("workspace metadata store is not configured")
        await store.update_status(
            record.workspace_record_id,
            status="cleanup_failed" if cleanup_error is not None else "cleaned",
            cleanup_error=cleanup_error,
        )

    def _workspace_export_in_progress(self, session_id: str) -> bool:
        return self._session_workspace_export_counts.get(session_id, 0) > 0

    def _begin_workspace_export(self, session_id: str) -> None:
        self._session_workspace_export_counts[session_id] = (
            self._session_workspace_export_counts.get(session_id, 0) + 1
        )

    def _end_workspace_export(self, session_id: str) -> None:
        count = self._session_workspace_export_counts.get(session_id, 0)
        if count <= 1:
            self._session_workspace_export_counts.pop(session_id, None)
            return
        self._session_workspace_export_counts[session_id] = count - 1

    async def _persist_workspace_record_for_session(self, session: Session) -> None:
        store = self._workspace_metadata_store
        if store is None:
            return
        workspace = _session_cloud_workspace(session)
        if workspace is None:
            return
        origin = session.origin or {}
        if (
            origin.get("placement_kind") != "cloud_workspace"
            or origin.get("workspace_source_kind") is None
        ):
            return

        provider = origin.get("workspace_provider") or "docker"
        provider_instance_id = origin.get("provider_instance_id")
        workspace_root_ref = origin.get("workspace_root_ref")
        workspace_host_label = origin.get("workspace_host_label")
        owner_label = origin.get("owner_label")
        source_kind = origin.get("workspace_source_kind")
        if (
            not isinstance(provider, str)
            or not isinstance(provider_instance_id, str)
            or not isinstance(workspace_root_ref, str)
            or not isinstance(workspace_host_label, str)
            or not isinstance(owner_label, str)
            or not isinstance(source_kind, str)
        ):
            raise RuntimeError(
                "cloud workspace session is missing durable workspace metadata"
            )

        source_ref: dict[str, JSONValue] = {}
        if workspace.runtime_profile is not None:
            source_ref["runtime_profile"] = workspace.runtime_profile
        await store.save(
            WorkspaceRecord(
                workspace_record_id=f"{session.id}:{workspace.workspace_id}",
                workspace_id=workspace.workspace_id,
                session_id=session.id,
                provider=provider,
                provider_instance_id=provider_instance_id,
                workspace_root_ref=workspace_root_ref,
                workspace_host_label=workspace_host_label,
                owner_label=owner_label,
                source_kind=source_kind,
                source_ref=source_ref,
                status="active",
                retention_policy="delete_on_close",
            )
        )

    async def export_workspace_archive(
        self,
        session_id: str,
        export_archive: Callable[[CloudWorkspaceRef], T],
    ) -> T:
        return await self._runtime_workspace_export_service.export_archive(
            session_id,
            export_archive,
        )
