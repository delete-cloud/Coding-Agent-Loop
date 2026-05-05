from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, TypeAlias

from .cloud import CloudWorkspaceClient

if TYPE_CHECKING:
    from ..ui.execution_binding import CloudWorkspaceBinding


CloudWorkspaceClientFactory: TypeAlias = Callable[
    ["CloudWorkspaceBinding"], CloudWorkspaceClient
]


class WorkspaceProvider(Protocol):
    def build_cloud_client_factory(
        self, config: dict[str, object]
    ) -> CloudWorkspaceClientFactory: ...


_WORKSPACE_PROVIDERS: dict[str, WorkspaceProvider] = {}


def register_workspace_provider(name: str, provider: WorkspaceProvider) -> None:
    normalized_name = name.strip().lower()
    if not normalized_name:
        raise ValueError("workspace provider name must be non-empty")
    _WORKSPACE_PROVIDERS[normalized_name] = provider


def cloud_client_factory_from_config(
    config: dict[str, object],
) -> CloudWorkspaceClientFactory:
    provider_name = config.get("provider")
    if not isinstance(provider_name, str) or not provider_name.strip():
        raise ValueError(
            "cloud_workspace.provider is required when cloud_workspace.enabled=true"
        )

    provider = _WORKSPACE_PROVIDERS.get(provider_name.strip().lower())
    if provider is None:
        raise ValueError(f"unsupported cloud workspace provider: {provider_name}")
    return provider.build_cloud_client_factory(config)
