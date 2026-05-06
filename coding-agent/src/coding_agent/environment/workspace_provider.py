from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Protocol, TypeAlias

from .cloud import CloudWorkspaceClient

if TYPE_CHECKING:
    from ..ui.execution_binding import CloudWorkspaceBinding


CloudWorkspaceClientFactory: TypeAlias = Callable[
    ["CloudWorkspaceBinding"], CloudWorkspaceClient
]
CloudWorkspaceSource: TypeAlias = Mapping[str, object]


class WorkspaceProvider(Protocol):
    def build_cloud_client_factory(
        self, config: dict[str, object]
    ) -> CloudWorkspaceClientFactory: ...

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
