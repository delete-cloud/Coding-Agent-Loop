from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from agentkit.environment import Environment
from coding_agent.environment import LocalEnvironment
from coding_agent.ui.execution_binding import (
    CloudWorkspaceBinding,
    ExecutionBinding,
    LocalExecutionBinding,
)


class BindingResolver(Protocol):
    def resolve_workspace_root(self, binding: ExecutionBinding) -> Path: ...

    def resolve_tool_config(self, binding: ExecutionBinding) -> dict[str, Any]: ...

    def resolve_environment(self, binding: ExecutionBinding) -> Environment: ...


class CloudBindingNotImplementedError(NotImplementedError):
    """Raised when callers request unresolved cloud workspace behavior."""


class DefaultBindingResolver:
    def resolve_workspace_root(self, binding: ExecutionBinding) -> Path:
        if isinstance(binding, LocalExecutionBinding):
            return Path(binding.workspace_root).expanduser().resolve()
        if isinstance(binding, CloudWorkspaceBinding):
            raise CloudBindingNotImplementedError(
                "cloud workspace resolution is not yet implemented"
            )
        raise ValueError(f"unsupported binding type: {type(binding).__name__}")

    def resolve_tool_config(self, binding: ExecutionBinding) -> dict[str, Any]:
        if isinstance(binding, LocalExecutionBinding):
            return {"workspace_root": str(self.resolve_workspace_root(binding))}
        if isinstance(binding, CloudWorkspaceBinding):
            raise CloudBindingNotImplementedError(
                "cloud workspace tool config is not yet implemented"
            )
        raise ValueError(f"unsupported binding type: {type(binding).__name__}")

    def resolve_environment(self, binding: ExecutionBinding) -> Environment:
        if isinstance(binding, LocalExecutionBinding):
            return LocalEnvironment(self.resolve_workspace_root(binding))
        if isinstance(binding, CloudWorkspaceBinding):
            raise CloudBindingNotImplementedError(
                "cloud workspace environment is not yet implemented"
            )
        raise ValueError(f"unsupported binding type: {type(binding).__name__}")
