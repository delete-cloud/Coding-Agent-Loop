from __future__ import annotations

from collections.abc import Callable, Coroutine, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol, cast


type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list[JSONValue] | dict[str, JSONValue]

WorkspaceStatus = Literal[
    "provisioning",
    "active",
    "idle",
    "retained",
    "stale",
    "cleaning",
    "cleaned",
    "cleanup_failed",
    "lost",
]
WorkspaceRetentionPolicy = Literal["delete_on_close", "ttl", "pinned", "manual"]


class AsyncPGWorkspacePool(Protocol):
    async def get_pool(self) -> object: ...


@dataclass(frozen=True)
class WorkspaceRecord:
    workspace_record_id: str
    workspace_id: str
    session_id: str
    provider: str
    provider_instance_id: str
    workspace_root_ref: str
    workspace_host_label: str
    owner_label: str
    source_kind: str
    source_ref: dict[str, JSONValue] = field(default_factory=dict)
    status: WorkspaceStatus = "provisioning"
    retention_policy: WorkspaceRetentionPolicy = "delete_on_close"
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    cleanup_error: str | None = None
    result_refs: dict[str, JSONValue] = field(default_factory=dict)
    resource_refs: dict[str, JSONValue] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PGWorkspaceMetadataStore:
    _CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS agent_remote_workspaces (
        workspace_record_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        provider TEXT NOT NULL,
        provider_instance_id TEXT NOT NULL,
        workspace_root_ref TEXT NOT NULL,
        workspace_host_label TEXT NOT NULL,
        owner_label TEXT NOT NULL,
        source_kind TEXT NOT NULL,
        source_ref JSONB NOT NULL DEFAULT '{}'::jsonb,
        status TEXT NOT NULL,
        retention_policy TEXT NOT NULL,
        last_used_at TIMESTAMPTZ,
        expires_at TIMESTAMPTZ,
        cleanup_error TEXT,
        result_refs JSONB NOT NULL DEFAULT '{}'::jsonb,
        resource_refs JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """
    _UPSERT_SQL = """
    INSERT INTO agent_remote_workspaces (
        workspace_record_id,
        workspace_id,
        session_id,
        provider,
        provider_instance_id,
        workspace_root_ref,
        workspace_host_label,
        owner_label,
        source_kind,
        source_ref,
        status,
        retention_policy,
        last_used_at,
        expires_at,
        cleanup_error,
        result_refs,
        resource_refs
    )
    VALUES (
        $1, $2, $3, $4, $5, $6, $7, $8, $9,
        $10::jsonb, $11, $12, $13, $14, $15,
        $16::jsonb, $17::jsonb
    )
    ON CONFLICT (workspace_record_id)
    DO UPDATE SET
        workspace_id = EXCLUDED.workspace_id,
        session_id = EXCLUDED.session_id,
        provider = EXCLUDED.provider,
        provider_instance_id = EXCLUDED.provider_instance_id,
        workspace_root_ref = EXCLUDED.workspace_root_ref,
        workspace_host_label = EXCLUDED.workspace_host_label,
        owner_label = EXCLUDED.owner_label,
        source_kind = EXCLUDED.source_kind,
        source_ref = EXCLUDED.source_ref,
        status = EXCLUDED.status,
        retention_policy = EXCLUDED.retention_policy,
        last_used_at = EXCLUDED.last_used_at,
        expires_at = EXCLUDED.expires_at,
        cleanup_error = EXCLUDED.cleanup_error,
        result_refs = EXCLUDED.result_refs,
        resource_refs = EXCLUDED.resource_refs,
        updated_at = NOW()
    """
    _SELECT_SQL = "SELECT * FROM agent_remote_workspaces WHERE workspace_record_id = $1"
    _SELECT_BY_SESSION_WORKSPACE_SQL = """
    SELECT * FROM agent_remote_workspaces
    WHERE session_id = $1 AND workspace_id = $2
    ORDER BY updated_at DESC
    LIMIT 1
    """
    _LIST_SQL = "SELECT * FROM agent_remote_workspaces ORDER BY workspace_record_id"
    _UPDATE_STATUS_SQL = """
    UPDATE agent_remote_workspaces
    SET status = $1, cleanup_error = $2, updated_at = NOW()
    WHERE workspace_record_id = $3
    """

    def __init__(self, *, pool: AsyncPGWorkspacePool) -> None:
        self._pool = pool
        self._schema_ready = False

    async def _ensure_schema(self) -> object:
        asyncpg_pool = await self._pool.get_pool()
        if not self._schema_ready:
            execute = getattr(asyncpg_pool, "execute", None)
            if not callable(execute):
                raise TypeError("postgres workspace metadata pool must expose execute")
            _ = await cast(Callable[..., Coroutine[object, object, object]], execute)(
                self._CREATE_TABLE_SQL
            )
            self._schema_ready = True
        return asyncpg_pool

    async def save(self, record: WorkspaceRecord) -> None:
        pool = await self._ensure_schema()
        execute = getattr(pool, "execute", None)
        if not callable(execute):
            raise TypeError("postgres workspace metadata pool must expose execute")
        _ = await cast(Callable[..., Coroutine[object, object, object]], execute)(
            self._UPSERT_SQL,
            record.workspace_record_id,
            record.workspace_id,
            record.session_id,
            record.provider,
            record.provider_instance_id,
            record.workspace_root_ref,
            record.workspace_host_label,
            record.owner_label,
            record.source_kind,
            record.source_ref,
            record.status,
            record.retention_policy,
            record.last_used_at,
            record.expires_at,
            record.cleanup_error,
            record.result_refs,
            record.resource_refs,
        )

    async def load(self, workspace_record_id: str) -> WorkspaceRecord | None:
        pool = await self._ensure_schema()
        fetchrow = getattr(pool, "fetchrow", None)
        if not callable(fetchrow):
            raise TypeError("postgres workspace metadata pool must expose fetchrow")
        row_obj = await cast(
            Callable[..., Coroutine[object, object, object]], fetchrow
        )(
            self._SELECT_SQL,
            workspace_record_id,
        )
        if row_obj is None:
            return None
        return _workspace_record_from_row(row_obj)

    async def load_for_session_workspace(
        self,
        *,
        session_id: str,
        workspace_id: str,
    ) -> WorkspaceRecord | None:
        pool = await self._ensure_schema()
        fetchrow = getattr(pool, "fetchrow", None)
        if not callable(fetchrow):
            raise TypeError("postgres workspace metadata pool must expose fetchrow")
        row_obj = await cast(
            Callable[..., Coroutine[object, object, object]], fetchrow
        )(
            self._SELECT_BY_SESSION_WORKSPACE_SQL,
            session_id,
            workspace_id,
        )
        if row_obj is None:
            return None
        return _workspace_record_from_row(row_obj)

    async def list(self) -> list[WorkspaceRecord]:
        pool = await self._ensure_schema()
        fetch = getattr(pool, "fetch", None)
        if not callable(fetch):
            raise TypeError("postgres workspace metadata pool must expose fetch")
        rows_obj = await cast(Callable[..., Coroutine[object, object, object]], fetch)(
            self._LIST_SQL
        )
        if not isinstance(rows_obj, list):
            raise TypeError("postgres workspace metadata list result must be a list")
        return [_workspace_record_from_row(row) for row in cast(list[object], rows_obj)]

    async def update_status(
        self,
        workspace_record_id: str,
        *,
        status: WorkspaceStatus,
        cleanup_error: str | None = None,
    ) -> None:
        pool = await self._ensure_schema()
        execute = getattr(pool, "execute", None)
        if not callable(execute):
            raise TypeError("postgres workspace metadata pool must expose execute")
        result = await cast(Callable[..., Coroutine[object, object, object]], execute)(
            self._UPDATE_STATUS_SQL,
            status,
            cleanup_error,
            workspace_record_id,
        )
        if result == "UPDATE 0":
            raise KeyError(workspace_record_id)


def _workspace_record_from_row(row: object) -> WorkspaceRecord:
    row_dict = _coerce_row_dict(row=row, context="postgres workspace metadata row")
    return WorkspaceRecord(
        workspace_record_id=_require_string(row_dict, "workspace_record_id"),
        workspace_id=_require_string(row_dict, "workspace_id"),
        session_id=_require_string(row_dict, "session_id"),
        provider=_require_string(row_dict, "provider"),
        provider_instance_id=_require_string(row_dict, "provider_instance_id"),
        workspace_root_ref=_require_string(row_dict, "workspace_root_ref"),
        workspace_host_label=_require_string(row_dict, "workspace_host_label"),
        owner_label=_require_string(row_dict, "owner_label"),
        source_kind=_require_string(row_dict, "source_kind"),
        source_ref=_optional_json_dict(row_dict.get("source_ref")),
        status=_workspace_status(row_dict.get("status")),
        retention_policy=_retention_policy(row_dict.get("retention_policy")),
        last_used_at=_optional_datetime(row_dict.get("last_used_at"), "last_used_at"),
        expires_at=_optional_datetime(row_dict.get("expires_at"), "expires_at"),
        cleanup_error=_optional_string(row_dict.get("cleanup_error"), "cleanup_error"),
        result_refs=_optional_json_dict(row_dict.get("result_refs")),
        resource_refs=_optional_json_dict(row_dict.get("resource_refs")),
        created_at=_optional_datetime(row_dict.get("created_at"), "created_at"),
        updated_at=_optional_datetime(row_dict.get("updated_at"), "updated_at"),
    )


def _coerce_row_dict(*, row: object, context: str) -> dict[str, object]:
    if isinstance(row, dict):
        return cast(dict[str, object], row)
    if not isinstance(row, Iterable):
        raise TypeError(f"{context} must be convertible to a dict")
    try:
        row_items = cast(Iterable[tuple[object, object]], row)
        row_dict_obj = dict(row_items)
    except Exception as exc:
        raise TypeError(f"{context} must be convertible to a dict") from exc
    return {str(key): value for key, value in row_dict_obj.items()}


def _require_string(row: dict[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise TypeError(f"postgres workspace metadata row must include string {key}")
    return value


def _optional_string(value: object, key: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"postgres workspace metadata row {key} must be a string")
    return value


def _optional_datetime(value: object, key: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise TypeError(f"postgres workspace metadata row {key} must be a datetime")
    return value


def _optional_json_dict(value: object) -> dict[str, JSONValue]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError("postgres workspace metadata JSON fields must be objects")
    return cast(dict[str, JSONValue], value)


def _workspace_status(value: object) -> WorkspaceStatus:
    valid: set[str] = {
        "provisioning",
        "active",
        "idle",
        "retained",
        "stale",
        "cleaning",
        "cleaned",
        "cleanup_failed",
        "lost",
    }
    if not isinstance(value, str) or value not in valid:
        raise TypeError("postgres workspace metadata row has invalid status")
    return cast(WorkspaceStatus, value)


def _retention_policy(value: object) -> WorkspaceRetentionPolicy:
    valid: set[str] = {"delete_on_close", "ttl", "pinned", "manual"}
    if not isinstance(value, str) or value not in valid:
        raise TypeError("postgres workspace metadata row has invalid retention_policy")
    return cast(WorkspaceRetentionPolicy, value)
