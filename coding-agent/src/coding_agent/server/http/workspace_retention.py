"""Workspace retention, publication refs, and archive response helpers."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from fastapi import HTTPException

from agentkit.result.models import ArtifactRef
from coding_agent.environment import (
    WorkspaceArchiveManifest,
    WorkspaceBranchPublication,
    WorkspaceInventoryEntry,
)
from coding_agent.runs import (
    CloudWorkspaceRef,
)
from coding_agent.server.schemas import (
    WorkspaceArchiveManifestResponse,
    WorkspaceRetentionPolicy,
    WorkspaceRetentionResponse,
    WorkspaceSummarySchema,
)
from coding_agent.server.stores.workspace_store import (
    JSONValue,
    WorkspaceRecord,
)

from coding_agent.server.http import _bindings
from coding_agent.server.http._bindings import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)


def _workspace_summary_response(
    entry: WorkspaceInventoryEntry,
) -> WorkspaceSummarySchema:
    return WorkspaceSummarySchema(
        workspace_id=entry.workspace_id,
        status=entry.status,
        updated_at=entry.updated_at,
    )


def _remote_retention_enabled() -> bool:
    return _bindings.module()._load_remote_retention_config().get("enabled") is True


def _configured_provider_instance_id() -> str | None:
    provider_instance_id = (
        _bindings.module()._load_cloud_workspace_config().get("provider_instance_id")
    )
    if isinstance(provider_instance_id, str) and provider_instance_id.strip():
        return provider_instance_id.strip()
    return None


def _workspace_record_summary_response(
    record: WorkspaceRecord,
) -> WorkspaceSummarySchema:
    updated_at = record.updated_at or record.created_at or datetime.now(UTC)
    local_provider_instance_id = _configured_provider_instance_id()
    return WorkspaceSummarySchema(
        workspace_id=record.workspace_id,
        status=record.status,
        updated_at=updated_at,
        session_id=record.session_id,
        provider=record.provider,
        provider_instance_id=record.provider_instance_id,
        workspace_host_label=record.workspace_host_label,
        source_kind=record.source_kind,
        retention_policy=record.retention_policy,
        expires_at=record.expires_at,
        cleanup_error=record.cleanup_error,
        result_refs=record.result_refs,
        is_local=(
            local_provider_instance_id is not None
            and record.provider_instance_id == local_provider_instance_id
        ),
    )


async def _local_workspace_record_for_provider_operation(
    workspace_id: str,
) -> WorkspaceRecord:
    record = (
        await _bindings.module().session_manager.load_workspace_record_by_workspace_id(
            workspace_id
        )
    )
    if record is None:
        raise HTTPException(
            status_code=404, detail=f"Workspace not found: {workspace_id}"
        )
    local_provider_instance_id = _configured_provider_instance_id()
    if local_provider_instance_id is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "cloud_workspace.provider_instance_id is required for "
                "provider-local workspace operations"
            ),
        )
    if record.provider_instance_id != local_provider_instance_id:
        raise HTTPException(
            status_code=409,
            detail=(
                "Workspace belongs to a different provider instance and cannot "
                "be operated by this server"
            ),
        )
    return record


def _retention_expires_at(
    *,
    retention_policy: WorkspaceRetentionPolicy,
    ttl_seconds: int | None,
) -> datetime | None:
    if retention_policy != "ttl":
        return None
    if ttl_seconds is None:
        configured_ttl = (
            _bindings.module()
            ._load_remote_retention_config()
            .get("default_ttl_seconds")
        )
        if not isinstance(configured_ttl, int) or configured_ttl <= 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    "ttl_seconds is required when retention_policy=ttl and "
                    "remote_retention.default_ttl_seconds is not configured"
                ),
            )
        ttl_seconds = configured_ttl
    return datetime.now(UTC) + timedelta(seconds=ttl_seconds)


async def _update_workspace_retention(
    workspace_id: str,
    *,
    retention_policy: WorkspaceRetentionPolicy,
    ttl_seconds: int | None,
) -> WorkspaceRetentionResponse:
    record = (
        await _bindings.module().session_manager.load_workspace_record_by_workspace_id(
            workspace_id
        )
    )
    if record is None:
        raise HTTPException(
            status_code=404, detail=f"Workspace not found: {workspace_id}"
        )
    expires_at = _retention_expires_at(
        retention_policy=retention_policy,
        ttl_seconds=ttl_seconds,
    )
    await _bindings.module().session_manager.update_workspace_record_retention(
        record.workspace_record_id,
        retention_policy=retention_policy,
        expires_at=expires_at,
        status="retained",
    )
    return WorkspaceRetentionResponse(
        workspace_id=record.workspace_id,
        retention_policy=retention_policy,
        ttl_seconds=ttl_seconds,
        status="retained",
    )


async def _persist_workspace_publication_refs(
    session_id: str,
    *,
    publication: WorkspaceBranchPublication,
    mode: Literal["branch", "pr"],
    pr_url: str | None,
) -> None:
    if not _remote_retention_enabled():
        return
    session = await _bindings.module().session_manager.get_session_async(session_id)
    target = session.default_run_target
    if target is None:
        return
    workspace = target.workspace
    if not isinstance(workspace, CloudWorkspaceRef):
        return
    record = (
        await _bindings.module().session_manager.load_workspace_record_by_workspace_id(
            workspace.workspace_id
        )
    )
    if record is None:
        return
    result_refs = dict(record.result_refs)
    result_refs["publication"] = {
        "mode": mode,
        "status": publication.status,
        "branch_name": publication.branch_name,
        "pushed_ref": publication.pushed_ref,
        "commit_sha": publication.commit_sha,
        "remote_url": publication.remote_url,
        "pr_url": pr_url,
        "error": publication.error,
        "artifact_ref": _artifact_ref_json(
            _workspace_publication_artifact_ref(
                session_id=session_id,
                publication=publication,
                mode=mode,
                pr_url=pr_url,
            )
        ),
    }
    await _bindings.module().session_manager.update_workspace_record_result_refs(
        record.workspace_record_id,
        result_refs=result_refs,
    )


def _workspace_publication_artifact_ref(
    *,
    session_id: str,
    publication: WorkspaceBranchPublication,
    mode: Literal["branch", "pr"],
    pr_url: str | None,
) -> ArtifactRef:
    metadata: dict[str, object] = {
        "session_id": session_id,
        "workspace_id": publication.workspace_id,
        "mode": mode,
        "status": publication.status,
        "branch_name": publication.branch_name,
        "pushed_ref": publication.pushed_ref,
        "commit_sha": publication.commit_sha,
        "remote_url": publication.remote_url,
        "pr_url": pr_url,
        "error": publication.error,
    }
    artifact_kind: Literal["branch", "pull_request"] = (
        "pull_request" if pr_url is not None else "branch"
    )
    uri = pr_url if pr_url is not None else publication.remote_url
    summary = _workspace_publication_artifact_summary(publication)
    return ArtifactRef(
        artifact_id=f"workspace:{publication.workspace_id}:publication",
        kind=artifact_kind,
        title="Workspace publication",
        summary=summary,
        uri=uri,
        metadata=metadata,
    )


def _workspace_publication_artifact_summary(
    publication: WorkspaceBranchPublication,
) -> str:
    if publication.status == "published" and publication.branch_name:
        if publication.commit_sha:
            return f"Published branch {publication.branch_name} at {publication.commit_sha}"
        return f"Published branch {publication.branch_name}"
    if publication.status == "partial" and publication.commit_sha:
        return f"Created local publication commit {publication.commit_sha}"
    return f"Workspace publication {publication.status}"


def _artifact_ref_json(artifact_ref: ArtifactRef) -> dict[str, JSONValue]:
    return {
        "artifact_id": artifact_ref.artifact_id,
        "kind": artifact_ref.kind,
        "title": artifact_ref.title,
        "summary": artifact_ref.summary,
        "uri": artifact_ref.uri,
        "metadata": cast(dict[str, JSONValue], artifact_ref.metadata),
        "producer_turn_id": artifact_ref.producer_turn_id,
    }


def _workspace_archive_manifest_response(
    manifest: WorkspaceArchiveManifest,
) -> WorkspaceArchiveManifestResponse:
    return WorkspaceArchiveManifestResponse(
        workspace_id=manifest.workspace_id,
        session_id=manifest.session_id,
        format=manifest.format,
        generated_at=manifest.generated_at,
        file_count=manifest.file_count,
        total_bytes=manifest.total_bytes,
        changed_files=manifest.changed_files,
        deleted_files=manifest.deleted_files,
        excluded_files=manifest.excluded_files,
        archive_sha256=manifest.archive_sha256,
    )


def _durable_workspace_retention_not_implemented() -> HTTPException:
    return HTTPException(
        status_code=501,
        detail="Durable remote workspace retention is not implemented yet.",
    )


__all__ = [
    "_artifact_ref_json",
    "_configured_provider_instance_id",
    "_durable_workspace_retention_not_implemented",
    "_local_workspace_record_for_provider_operation",
    "_persist_workspace_publication_refs",
    "_remote_retention_enabled",
    "_retention_expires_at",
    "_update_workspace_retention",
    "_workspace_archive_manifest_response",
    "_workspace_publication_artifact_ref",
    "_workspace_publication_artifact_summary",
    "_workspace_record_summary_response",
    "_workspace_summary_response",
]
