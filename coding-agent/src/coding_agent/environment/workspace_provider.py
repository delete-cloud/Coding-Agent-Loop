from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias

from .cloud import CloudWorkspaceClient

if TYPE_CHECKING:
    from ..ui.execution_binding import CloudWorkspaceBinding


CloudWorkspaceClientFactory: TypeAlias = Callable[
    ["CloudWorkspaceBinding"], CloudWorkspaceClient
]
CloudWorkspaceSource: TypeAlias = Mapping[str, object]
WorkspaceStatus: TypeAlias = Literal[
    "active", "stale", "cleaning", "cleaned", "cleanup_failed"
]


@dataclass(frozen=True)
class WorkspaceInventoryEntry:
    workspace_id: str
    status: WorkspaceStatus
    updated_at: datetime


@dataclass(frozen=True)
class WorkspaceArchiveManifest:
    workspace_id: str
    session_id: str | None
    format: Literal["tar.gz"]
    generated_at: datetime
    file_count: int
    total_bytes: int
    changed_files: list[str]
    deleted_files: list[str]
    excluded_files: list[str]
    archive_sha256: str | None


class WorkspaceProvider(Protocol):
    def build_cloud_client_factory(
        self, config: dict[str, object]
    ) -> CloudWorkspaceClientFactory: ...

    def check_readiness(self, config: dict[str, object]) -> bool: ...

    def provision_cloud_workspace_binding(
        self,
        config: dict[str, object],
        source: CloudWorkspaceSource,
    ) -> "CloudWorkspaceBinding": ...

    def cleanup_cloud_workspace_binding(
        self,
        config: dict[str, object],
        binding: "CloudWorkspaceBinding",
    ) -> None: ...

    def import_workspace_archive(
        self,
        config: dict[str, object],
        binding: "CloudWorkspaceBinding",
        archive_base64: str,
    ) -> None: ...

    def export_workspace_archive(
        self,
        config: dict[str, object],
        binding: "CloudWorkspaceBinding",
    ) -> str: ...

    def cleanup_stale_cloud_workspaces(
        self,
        config: dict[str, object],
    ) -> int: ...

    def list_cloud_workspaces(
        self,
        config: dict[str, object],
        *,
        active_workspace_ids: set[str] | None = None,
    ) -> list[WorkspaceInventoryEntry]: ...

    def get_cloud_workspace(
        self,
        config: dict[str, object],
        workspace_id: str,
        *,
        active_workspace_ids: set[str] | None = None,
    ) -> WorkspaceInventoryEntry: ...

    def cleanup_cloud_workspace(
        self,
        config: dict[str, object],
        workspace_id: str,
        *,
        active_workspace_ids: set[str] | None = None,
    ) -> WorkspaceInventoryEntry: ...

    def export_workspace_archive_by_id(
        self,
        config: dict[str, object],
        workspace_id: str,
    ) -> str: ...

    def workspace_archive_manifest(
        self,
        config: dict[str, object],
        workspace_id: str,
        *,
        session_id: str | None = None,
    ) -> WorkspaceArchiveManifest: ...


_WORKSPACE_PROVIDERS: dict[str, WorkspaceProvider] = {}


def register_workspace_provider(name: str, provider: WorkspaceProvider) -> None:
    normalized_name = name.strip().lower()
    if not normalized_name:
        raise ValueError("workspace provider name must be non-empty")
    _WORKSPACE_PROVIDERS[normalized_name] = provider


def cloud_client_factory_from_config(
    config: dict[str, object],
) -> CloudWorkspaceClientFactory:
    provider = _provider_from_config(config)
    return provider.build_cloud_client_factory(config)


def cloud_workspace_ready_from_config(config: dict[str, object]) -> bool:
    provider = _provider_from_config(config)
    return bool(provider.check_readiness(config))


def provision_cloud_binding_from_config(
    config: dict[str, object],
    source: CloudWorkspaceSource,
) -> "CloudWorkspaceBinding":
    provider = _provider_from_config(config)
    return provider.provision_cloud_workspace_binding(config, source)


def cleanup_cloud_binding_from_config(
    config: dict[str, object],
    binding: "CloudWorkspaceBinding",
) -> None:
    provider = _provider_from_config(config)
    provider.cleanup_cloud_workspace_binding(config, binding)


def import_workspace_archive_from_config(
    config: dict[str, object],
    binding: "CloudWorkspaceBinding",
    archive_base64: str,
) -> None:
    provider = _provider_from_config(config)
    provider.import_workspace_archive(config, binding, archive_base64)


def export_workspace_archive_from_config(
    config: dict[str, object],
    binding: "CloudWorkspaceBinding",
) -> str:
    provider = _provider_from_config(config)
    return provider.export_workspace_archive(config, binding)


def cleanup_stale_cloud_workspaces_from_config(config: dict[str, object]) -> int:
    provider = _provider_from_config(config)
    return provider.cleanup_stale_cloud_workspaces(config)


def list_cloud_workspaces_from_config(
    config: dict[str, object],
    *,
    active_workspace_ids: set[str] | None = None,
) -> list[WorkspaceInventoryEntry]:
    provider = _provider_from_config(config)
    return provider.list_cloud_workspaces(
        config,
        active_workspace_ids=active_workspace_ids,
    )


def get_cloud_workspace_from_config(
    config: dict[str, object],
    workspace_id: str,
    *,
    active_workspace_ids: set[str] | None = None,
) -> WorkspaceInventoryEntry:
    provider = _provider_from_config(config)
    return provider.get_cloud_workspace(
        config,
        workspace_id,
        active_workspace_ids=active_workspace_ids,
    )


def cleanup_cloud_workspace_from_config(
    config: dict[str, object],
    workspace_id: str,
    *,
    active_workspace_ids: set[str] | None = None,
) -> WorkspaceInventoryEntry:
    provider = _provider_from_config(config)
    return provider.cleanup_cloud_workspace(
        config,
        workspace_id,
        active_workspace_ids=active_workspace_ids,
    )


def export_workspace_archive_by_id_from_config(
    config: dict[str, object],
    workspace_id: str,
) -> str:
    provider = _provider_from_config(config)
    return provider.export_workspace_archive_by_id(config, workspace_id)


def workspace_archive_manifest_from_config(
    config: dict[str, object],
    workspace_id: str,
    *,
    session_id: str | None = None,
) -> WorkspaceArchiveManifest:
    provider = _provider_from_config(config)
    return provider.workspace_archive_manifest(
        config,
        workspace_id,
        session_id=session_id,
    )


def _provider_from_config(config: dict[str, object]) -> WorkspaceProvider:
    provider_name = config.get("provider")
    if not isinstance(provider_name, str) or not provider_name.strip():
        raise ValueError(
            "cloud_workspace.provider is required when cloud_workspace.enabled=true"
        )

    provider = _WORKSPACE_PROVIDERS.get(provider_name.strip().lower())
    if provider is None:
        raise ValueError(f"unsupported cloud workspace provider: {provider_name}")
    return provider
