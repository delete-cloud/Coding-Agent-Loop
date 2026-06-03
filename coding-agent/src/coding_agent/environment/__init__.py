from __future__ import annotations

from typing import Any

from .cloud import (
    CloudCommandResult,
    CloudEnvironment,
    CloudWorkspaceClient,
)
from .docker_workspace_provider import DockerCloudWorkspaceClient
from .local import LocalEnvironment
from .workspace_provider import (
    cleanup_cloud_binding_from_config,
    cleanup_cloud_workspace_from_config,
    cleanup_stale_cloud_workspaces_from_config,
    CloudWorkspaceClientFactory,
    CloudWorkspaceSource,
    WorkspaceArchiveManifest,
    WorkspaceBranchPublication,
    WorkspaceDiff,
    WorkspaceDiffFile,
    WorkspaceInventoryEntry,
    WorkspacePatch,
    WorkspaceProvider,
    WorkspaceProviderCapabilities,
    cloud_client_factory_from_config,
    cloud_workspace_ready_from_config,
    export_workspace_archive_by_id_from_config,
    export_workspace_archive_from_config,
    get_cloud_workspace_from_config,
    import_workspace_archive_from_config,
    list_cloud_workspaces_from_config,
    publish_workspace_branch_from_config,
    provision_cloud_binding_from_config,
    register_workspace_provider,
    workspace_provider_capabilities_from_config,
    workspace_archive_manifest_from_config,
    workspace_diff_from_config,
    workspace_patch_from_config,
)


def __getattr__(name: str) -> Any:
    if name in {"SandboxedEnvironment", "sandbox_environment"}:
        from .sandboxed import SandboxedEnvironment, sandbox_environment

        exports = {
            "SandboxedEnvironment": SandboxedEnvironment,
            "sandbox_environment": sandbox_environment,
        }
        return exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "CloudCommandResult",
    "DockerCloudWorkspaceClient",
    "CloudEnvironment",
    "CloudWorkspaceClient",
    "CloudWorkspaceClientFactory",
    "CloudWorkspaceSource",
    "cleanup_cloud_binding_from_config",
    "cleanup_cloud_workspace_from_config",
    "cleanup_stale_cloud_workspaces_from_config",
    "LocalEnvironment",
    "SandboxedEnvironment",
    "WorkspaceArchiveManifest",
    "WorkspaceBranchPublication",
    "WorkspaceDiff",
    "WorkspaceDiffFile",
    "WorkspaceInventoryEntry",
    "WorkspacePatch",
    "WorkspaceProvider",
    "WorkspaceProviderCapabilities",
    "cloud_client_factory_from_config",
    "cloud_workspace_ready_from_config",
    "export_workspace_archive_by_id_from_config",
    "export_workspace_archive_from_config",
    "get_cloud_workspace_from_config",
    "import_workspace_archive_from_config",
    "list_cloud_workspaces_from_config",
    "publish_workspace_branch_from_config",
    "provision_cloud_binding_from_config",
    "register_workspace_provider",
    "sandbox_environment",
    "workspace_provider_capabilities_from_config",
    "workspace_archive_manifest_from_config",
    "workspace_diff_from_config",
    "workspace_patch_from_config",
]
