from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from agentkit.environment import Environment
from coding_agent.environment import CloudEnvironment, CloudWorkspaceClient, LocalEnvironment
from coding_agent.server.execution_binding import (
    CloudWorkspaceBinding,
    ExecutionBinding,
    LocalExecutionBinding,
)


CloudClientFactory = Callable[[CloudWorkspaceBinding], CloudWorkspaceClient]


class BindingResolver(Protocol):
    def resolve_tool_config(self, binding: ExecutionBinding) -> dict[str, object]: ...

    def resolve_environment(self, binding: ExecutionBinding) -> Environment: ...


class CloudBindingNotImplementedError(NotImplementedError):
    """Raised when callers request unresolved cloud workspace behavior."""


class DefaultBindingResolver:
    def __init__(
        self,
        *,
        cloud_client_factory: CloudClientFactory | None = None,
    ) -> None:
        self._cloud_client_factory: CloudClientFactory | None = cloud_client_factory

    def resolve_workspace_root(self, binding: ExecutionBinding) -> Path:
        if isinstance(binding, LocalExecutionBinding):
            return Path(binding.workspace_root).expanduser().resolve()
        if isinstance(binding, CloudWorkspaceBinding):
            raise CloudBindingNotImplementedError(
                "cloud workspace resolution is not yet implemented"
            )
        raise ValueError(f"unsupported binding type: {type(binding).__name__}")

    def resolve_tool_config(self, binding: ExecutionBinding) -> dict[str, object]:
        if isinstance(binding, LocalExecutionBinding):
            return {"workspace_root": str(self.resolve_workspace_root(binding))}
        if isinstance(binding, CloudWorkspaceBinding):
            return self._resolve_cloud_environment(binding).tool_config()
        raise ValueError(f"unsupported binding type: {type(binding).__name__}")

    def resolve_environment(self, binding: ExecutionBinding) -> Environment:
        if isinstance(binding, LocalExecutionBinding):
            return LocalEnvironment(self.resolve_workspace_root(binding))
        if isinstance(binding, CloudWorkspaceBinding):
            return self._resolve_cloud_environment(binding)
        raise ValueError(f"unsupported binding type: {type(binding).__name__}")

    def _resolve_cloud_environment(
        self,
        binding: CloudWorkspaceBinding,
    ) -> CloudEnvironment:
        if self._cloud_client_factory is None:
            raise CloudBindingNotImplementedError(
                "cloud workspace environment is not yet implemented"
            )
        return CloudEnvironment(self._cloud_client_factory(binding))
